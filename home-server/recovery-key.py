#!/usr/bin/env python3
"""Operator-only age key custody. Private bytes stay in memory and native secret stores.

No private stdout, shell interpolation, plaintext temporary files, or Terraform values.
prepare stores in an explicit local login keychain before publishing the public recipient.
publish is separately invoked, never replaces an existing AWS key, and is retryable.
verify exercises each store independently with a disposable encrypted challenge.
"""

import argparse
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


class CustodyError(Exception):
    pass


def run(argv, payload=None):
    try:
        result = subprocess.run(argv, input=payload, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise CustodyError(f"{Path(argv[0]).name} unavailable or timed out") from None
    if result.returncode:
        # Command output can contain sensitive input; never relay it to logs/chat.
        raise CustodyError(f"{Path(argv[0]).name} failed (exit {result.returncode}); output suppressed")
    return result.stdout


def validate_identity(value):
    lines = value.decode("ascii").splitlines()
    keys = [line for line in lines if line and not line.startswith("#")]
    if len(keys) != 1 or not re.fullmatch(r"AGE-SECRET-KEY-1[0-9A-Z]{58}", keys[0]):
        raise CustodyError("Expected one native age v1 identity; value suppressed")
    return (keys[0] + "\n").encode("ascii")


def recipient(identity):
    value = run(["age-keygen", "-y"], validate_identity(identity)).decode("ascii").strip()
    if not re.fullmatch(r"age1[0-9a-z]{58}", value):
        raise CustodyError("Unexpected age recipient format")
    return value


class LocalKeychain:
    """Legacy file-keychain APIs target login.keychain-db explicitly, never iCloud.

    Native APIs avoid a password in `security -w SECRET` process arguments. They create
    a generic password with the creator's default access control, not all-app access.
    No updates/deletes or unlock/password prompts are performed.
    """

    def __init__(self, path, service, account):
        if sys.platform != "darwin" or path.name != "login.keychain-db" or not path.is_file():
            raise CustodyError("Use this Mac's existing local login.keychain-db")
        self.service, self.account = service.encode(), account.encode()
        self.api = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        ptr, u32 = ctypes.c_void_p, ctypes.c_uint32
        signatures = {
            "SecKeychainSetUserInteractionAllowed": [ctypes.c_bool],
            "SecKeychainOpen": [ctypes.c_char_p, ctypes.POINTER(ptr)],
            "SecKeychainFindGenericPassword": [ptr, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
                                              ctypes.POINTER(u32), ctypes.POINTER(ptr), ptr],
            "SecKeychainAddGenericPassword": [ptr, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
                                             u32, ptr, ptr],
            "SecKeychainItemFreeContent": [ptr, ptr],
        }
        for name, args in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, ctypes.c_int32
        self.check(self.api.SecKeychainSetUserInteractionAllowed(False))
        self.ref = ptr()
        self.check(self.api.SecKeychainOpen(os.fsencode(path), ctypes.byref(self.ref)))

    @staticmethod
    def check(code):
        if code:
            raise CustodyError(f"Keychain OSStatus {code}; unlock/authorize locally if needed, never send passwords")

    def get(self):
        length, content = ctypes.c_uint32(), ctypes.c_void_p()
        code = self.api.SecKeychainFindGenericPassword(
            self.ref, len(self.service), self.service, len(self.account), self.account,
            ctypes.byref(length), ctypes.byref(content), None)
        if code == -25300:  # errSecItemNotFound; every other failure is fatal.
            return None
        self.check(code)
        try:
            return validate_identity(ctypes.string_at(content, length.value))
        finally:
            self.api.SecKeychainItemFreeContent(None, content)

    def add(self, identity):
        value = validate_identity(identity)
        self.check(self.api.SecKeychainAddGenericPassword(
            self.ref, len(self.service), self.service, len(self.account), self.account,
            len(value), ctypes.c_char_p(value), None))


def prepare(keychain, public_path):
    identity = keychain.get()
    created = identity is None
    if created:
        if public_path.exists():
            raise CustodyError("Recipient already exists but keychain item is missing; recover the original key")
        identity = validate_identity(run(["age-keygen"]))
        keychain.add(identity)
        if keychain.get() != identity:
            raise CustodyError("Keychain readback mismatch; preserve item and investigate")
    public = recipient(identity)
    if public_path.exists():
        if public_path.read_text().strip() != public:
            raise CustodyError("Existing public recipient differs; refusing replacement")
    else:
        with public_path.open("x") as stream:
            stream.write(public + "\n")
    return {"keychain_created": created, "recipient": public, "local_only": True}


class AWSSecret:
    def __init__(self, profile, region, secret_id, operator_arn):
        self.base = ["aws", "--profile", profile, "--region", region,
                     "--no-cli-pager", "--no-cli-auto-prompt"]
        self.secret_id = secret_id
        caller = self.call("sts", "get-caller-identity")
        if caller.get("Arn") != operator_arn:
            raise CustodyError("AWS caller differs from the selected recovery operator")
        metadata = self.call("secretsmanager", "describe-secret", {"SecretId": secret_id})
        if metadata.get("DeletedDate") or metadata.get("RotationEnabled"):
            raise CustodyError("Secret is scheduled for deletion or automatic rotation; stop")

    def call(self, service, action, request=None):
        argv = self.base + [service, action, "--output", "json"]
        if request is None:
            return json.loads(run(argv))
        public_request = dict(request)
        private_value = public_request.pop("SecretString", None)
        # CLI input JSON can be parsed more than once: use literal NON-SECRET metadata.
        # The single SecretString parameter reads stdin once, never argv/env/disk.
        argv += ["--cli-input-json", json.dumps(public_request)]
        if private_value is not None:
            if service != "secretsmanager" or action != "put-secret-value":
                raise CustodyError("Private input is restricted to PutSecretValue")
            argv += ["--secret-string", "file:///dev/stdin"]
        return json.loads(run(argv, private_value.encode() if private_value is not None else None))

    def get(self):
        response = self.call("secretsmanager", "get-secret-value", {"SecretId": self.secret_id})
        return validate_identity(response["SecretString"].encode())

    def publish(self, identity):
        identity = validate_identity(identity)
        metadata = self.call("secretsmanager", "describe-secret", {"SecretId": self.secret_id})
        if metadata.get("VersionIdsToStages"):
            if self.get() != identity:
                raise CustodyError("AWS contains a different key; refusing overwrite/rotation")
            return {"aws_value_created": False, "aws_readback_matches": True}
        version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.secret_id + ":" + recipient(identity)))
        self.call("secretsmanager", "put-secret-value", {
            "SecretId": self.secret_id, "ClientRequestToken": version_id,
            "SecretString": identity.decode(), "VersionStages": ["AWSCURRENT"],
        })
        if self.get() != identity:
            raise CustodyError("AWS readback mismatch; preserve the Keychain copy")
        return {"aws_value_created": True, "version_id": version_id, "aws_readback_matches": True}


def verify(identity, expected_recipient):
    if recipient(identity) != expected_recipient:
        raise CustodyError("Recovered key does not match the published recipient")
    challenge = b"Driftplain HM2 key custody proof; no production data.\n" + os.urandom(128)
    encrypted = run(["age", "--encrypt", "-r", expected_recipient], challenge)
    # Only ciphertext touches disk; the recovered private key goes to age's stdin.
    with tempfile.TemporaryDirectory(prefix="home-server-key-proof-") as folder:
        ciphertext = Path(folder) / "challenge.age"
        ciphertext.write_bytes(encrypted)
        recovered = run(["age", "--decrypt", "-i", "-", str(ciphertext)], identity)
    if recovered != challenge:
        raise CustodyError("Decryption proof mismatch")
    return {"decryption_verified": True, "challenge_sha256": hashlib.sha256(challenge).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "publish", "verify"])
    parser.add_argument("--source", choices=["keychain", "aws"])
    parser.add_argument("--recipient-file", required=True, type=Path)
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--keychain", type=Path)
    parser.add_argument("--account")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--operator-arn")
    args = parser.parse_args()
    if not re.fullmatch(r"modelmatch/home-server/recovery-key-v[1-9][0-9]*", args.secret_id):
        parser.error("Use the versioned recovery-key name, never runtime app secrets")
    if args.command == "verify" and not args.source:
        parser.error("verify requires --source")
    local = args.command in ("prepare", "publish") or args.source == "keychain"
    remote = args.command == "publish" or args.source == "aws"
    if local and not (args.keychain and args.account):
        parser.error("Local custody requires explicit --keychain and --account")
    if remote and not (args.profile and args.region and args.operator_arn):
        parser.error("AWS custody requires explicit --profile, --region and --operator-arn")
    keychain = LocalKeychain(args.keychain, args.secret_id, args.account) if local else None
    aws = AWSSecret(args.profile, args.region, args.secret_id, args.operator_arn) if remote else None
    if args.command == "prepare":
        result = prepare(keychain, args.recipient_file)
    else:
        expected = args.recipient_file.read_text().strip()
        identity = aws.get() if args.source == "aws" else keychain.get()
        if identity is None or recipient(identity) != expected:
            raise CustodyError("Stored key missing or different; recover it, never silently replace it")
        result = aws.publish(identity) if args.command == "publish" else verify(identity, expected)
    result.update(command=args.command, source=args.source,
                  verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except CustodyError as error:
        print(str(error), file=sys.stderr)  # Only fixed, sanitized messages above.
        sys.exit(1)
    except (OSError, ValueError, KeyError):
        # Even parsing/SDK exceptions can embed secret values. Emit no traceback/input.
        print("Recovery-key operation failed; private output suppressed. Preserve existing copies; consult docs/RECOVERY-KEY.md.", file=sys.stderr)
        sys.exit(1)
