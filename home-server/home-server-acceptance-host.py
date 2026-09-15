#!/usr/bin/env python3
"""Root-only, operator-driven identity acceptance. Credentials stay in process memory.

Not a workload, credential service or scheduler. Only STS and tiny encrypted S3 PUTs.
The run process retains its original sessions across operator-applied containment.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys

ROOT = Path('/var/lib/driftplain/home-server-identity/acceptance')
HELPER_SHA256 = 'beec9ed1c492d93db809890f16713e3556353294b823c2184ad4e891f1b2b54d'
PROBES = ('crl-bootstrap', 'revocation')


def emit(value):
    print(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **value}), flush=True)


def run(command, payload=None):
    result = subprocess.run(command, input=payload, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError('acceptance subprocess failed')
    return result.stdout


def prepare(probe):
    if probe not in PROBES:
        raise ValueError('unknown probe')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = ROOT / (probe + '.key')
    if not key.exists():
        data = run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072'])
        with key.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return run(['openssl', 'req', '-new', '-sha256', '-key', str(key),
                '-subj', '/CN=driftplain-home-server-backup']).decode()


def material(identity):
    if identity in PROBES:
        return (ROOT / (identity + '.crt')).read_bytes(), (ROOT / (identity + '.key')).read_bytes()
    if identity not in ('backup', 'bedrock'):
        raise ValueError('unexpected identity')
    spec = importlib.util.spec_from_file_location('leaf', '/opt/home-server/home-server-leaf.py')
    leaf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(leaf)
    ns, secret, _ = leaf.HOME_SERVER_TARGETS[identity]
    data = json.loads(leaf.kubectl(ns, 'get', 'secret', secret, '-o', 'json'))['data']
    return base64.b64decode(data['tls.crt'], validate=True), base64.b64decode(data['tls.key'], validate=True)


def exchange(identity, metadata, *, role=None, profile=None, anchor=None, expect_denied=False):
    helper = ROOT / 'aws_signing_helper'
    if hashlib.sha256(helper.read_bytes()).hexdigest() != HELPER_SHA256:
        raise ValueError('helper checksum mismatch')
    cert, key = material(identity)
    selected = metadata['bedrock' if identity == 'bedrock' else 'backup']
    fds = []
    try:
        for name, data in [('certificate', cert), ('private-key', key)]:
            fd = os.memfd_create('home-server-acceptance-' + name, os.MFD_CLOEXEC)
            fds.append(fd)
            os.fchmod(fd, 0o600)
            os.write(fd, data)
            os.lseek(fd, 0, os.SEEK_SET)
        command = [str(helper), 'credential-process', '--certificate', '/proc/self/fd/' + str(fds[0]),
                   '--private-key', '/proc/self/fd/' + str(fds[1]), '--role-arn', role or selected['role_arn'],
                   '--profile-arn', profile or selected['profile_arn'], '--trust-anchor-arn', anchor or selected['trust_anchor_arn'],
                   '--region', 'ap-south-1', '--session-duration', '900']
        env = {k: v for k, v in os.environ.items() if not k.startswith('AWS_')}
        env.update(AWS_EC2_METADATA_DISABLED='true', AWS_CONFIG_FILE='/dev/null', AWS_SHARED_CREDENTIALS_FILE='/dev/null')
        result = subprocess.run(command, capture_output=True, pass_fds=fds, env=env, timeout=40)
        if result.returncode:
            # An AWS denial must be distinguished from a local helper/transport failure.
            match = re.search(rb'(AccessDeniedException|ValidationException|ForbiddenException)', result.stderr)
            if expect_denied and match:
                return {'denied': True, 'aws_error': match.group(1).decode()}
            raise RuntimeError('expected credential exchange did not succeed')
        if expect_denied:
            raise ValueError('unexpected credential exchange success')
        value = json.loads(result.stdout)
        if value['Version'] != 1 or not all(value.get(k) for k in ('AccessKeyId', 'SecretAccessKey', 'SessionToken', 'Expiration')):
            raise ValueError('invalid credential envelope')
        expiration = datetime.fromisoformat(value['Expiration'].replace('Z', '+00:00'))
        if not 0 < (expiration - datetime.now(timezone.utc)).total_seconds() <= 930:
            raise ValueError('unexpected session duration')
        return value
    finally:
        for fd in fds:
            os.close(fd)


def client(service, credentials):
    if service not in ('sts', 's3'):
        raise ValueError('acceptance only permits STS and S3 clients')
    import boto3
    from botocore.config import Config
    return boto3.client(service, region_name='ap-south-1', aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'], aws_session_token=credentials['SessionToken'],
                        config=Config(connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 1}))


def session_check(identity, credentials):
    result = client('sts', credentials).get_caller_identity()
    role = 'bedrock' if identity == 'bedrock' else 'backup'
    if not result['Arn'].startswith('arn:aws:sts::957261948820:assumed-role/modelmatch-home-server-' + role + '/'):
        raise ValueError('unexpected assumed role')
    return {'identity': identity, 'assumed_role_arn': result['Arn'], 'expiration': credentials['Expiration']}


def put_probe(s3, name):
    from botocore.exceptions import ClientError
    payload = (ROOT / 'probe.age').read_bytes()
    if not payload.startswith(b'age-encryption.org/v1'):
        raise ValueError('acceptance payload must be encrypted')
    try:
        result = s3.put_object(Bucket='modelmatch-home-server-backups-957261948820',
                               Key='recovery/identity-acceptance/20260915/' + name + '.age', Body=payload,
                               ServerSideEncryption='AES256',
                               ChecksumSHA256=base64.b64encode(hashlib.sha256(payload).digest()).decode())
        return {'allowed': True, 'version_id': result['VersionId']}
    except ClientError as error:
        code = error.response['Error']['Code']
        if code not in ('AccessDenied', 'ExpiredToken'):
            raise
        return {'allowed': False, 'aws_error': code}


def acceptance():
    metadata = json.loads((ROOT / 'metadata.json').read_text())
    alternate = json.loads((ROOT / 'alternate-anchor.json').read_text())['trust_anchor_arn']
    sessions = {identity: exchange(identity, metadata) for identity in ('backup', 'bedrock', 'revocation')}
    for identity, value in sessions.items():
        emit({'phase': 'valid', **session_check(identity, value)})
    emit({'phase': 'wrong-role', **exchange('backup', metadata, role=metadata['bedrock']['role_arn'],
                                           profile=metadata['bedrock']['profile_arn'], expect_denied=True)})
    emit({'phase': 'wrong-profile', **exchange('backup', metadata, profile=metadata['bedrock']['profile_arn'], expect_denied=True)})
    emit({'phase': 'wrong-anchor', **exchange('backup', metadata, anchor=alternate, expect_denied=True)})
    s3 = client('s3', sessions['backup'])
    witness = put_probe(s3, 'before-deny')
    if not witness['allowed']:
        raise ValueError('allowed permission witness failed')
    emit({'phase': 'before-deny', **witness})
    emit({'phase': 'ready', 'latest_session_expiration': max(v['Expiration'] for v in sessions.values())})
    for line in sys.stdin:
        command = line.strip()
        if command == 'check-deny':
            emit({'phase': 'after-deny', **put_probe(s3, 'after-deny')})
        elif command == 'check-revocation':
            emit({'phase': 'revoked-leaf', **exchange('revocation', metadata, expect_denied=True)})
            value = exchange('backup', metadata)
            emit({'phase': 'unrevoked-control', **session_check('backup', value)})
        elif command == 'check-shutdown':
            for identity in ('backup', 'bedrock'):
                emit({'phase': 'disabled', 'identity': identity, **exchange(identity, metadata, expect_denied=True)})
        elif command == 'check-expired':
            emit({'phase': 'old-session-after-expiry', **put_probe(s3, 'after-expiry')})
        elif command == 'exit':
            emit({'phase': 'exiting'})
            return
        else:
            raise ValueError('unknown acceptance command')


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'run'))
    parser.add_argument('--probe', choices=PROBES)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('requires sudo -n')
    os.umask(0o077)
    try:
        if args.mode == 'prepare':
            emit({'probe': args.probe, 'csr': prepare(args.probe)})
        else:
            acceptance()
    except Exception as error:
        # Never print SDK/helper exception text or credential envelopes.
        emit({'phase': 'failed', 'error_type': type(error).__name__})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
