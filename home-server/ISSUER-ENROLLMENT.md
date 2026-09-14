# HM2 operational enrollment approval package — September 13, 2026

**Operational scope approved and verified September 13; source review approved September 14.** Steve's
explicit approval covered enrollment, encrypted Mac/S3 backup, public home checkpoint and
independent AWS-key recovery verification. [Operational evidence](hm2-issuer-enrollment-evidence.json)
records success with an empty ledger; no leaf, AWS identity or scheduler was created.
On September 14, Steve authorized committing, opening PRs and merging these records.
That publication approval does not expand the operational scope below.
This is the concrete operator setup
for the merged [issuer runbook](ISSUER.md), not a new issuer implementation. Infra PR #21
is merged at `e94f2b111a64670b3aeccf69f6743a8cf272ab84`; umbrella PR #23 is merged at
`4947171f633388c4535508d36a61770ab4711a8b`. All five canonical working trees were clean
on entry. Infra already had `feature/e21-hm2-operational-issuer` checked out at its merge;
its reflog showed only that checkout, with no intervening changes. The umbrella now uses
the same local branch name. Backend, frontend and GitOps are unchanged.

## Prepared on the Mac

Operator root: `/Users/steve/.local/share/driftplain/home-server-identity` (mode `0700`).
Paths in this table are relative to that root unless absolute.

| Asset | Concrete location / verification |
|---|---|
| Stable interpreter | `venv/bin/python3`, Python **3.12.13**, copied executable, not a symlink or disposable `uv --with` environment |
| Base standard library/runtime | `/Users/steve/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none`; preserve this exact version directory while the venv depends on it |
| Dependencies | boto3/botocore **1.43.24**, cryptography **50.0.1**; all ten resolved packages in `requirements.lock.txt` |
| Unchanged source copies | `home-server-issuer.py`, `home-server-renew.py`, `recovery-key.py`, `recovery-key-v1.recipient` (mode `0600`) |
| Encryption executables | Copies `bin/age` and `bin/age-keygen`, **1.3.2** (mode `0700`); no dependency on later Homebrew age upgrades |
| Enrollment/renewal config | `renewal.json`, derived from the merged example; only `age_binary` changed to the stable copy; `enabled=false`, fingerprint placeholder retained |
| Isolated recovery config | `recovery-drill.json`, also disabled; original CA/ledger/Keychain paths deliberately replaced as described below |
| Explicit shell setup | `operator.env`: stable PATH, named operator paths, core dumps disabled, `umask 077`, bytecode writes disabled, CLI pager/auto-prompt off; installs no job |
| Preparation record | `preparation-manifest.json`: executable/source/config SHA-256 values, dependency versions, path checks and operational-action exclusions |
| External operator programs | `/opt/homebrew/bin/aws` (2.36.18), `/opt/homebrew/bin/openssl` (3.6.3), `/usr/bin/ssh`; AWS/OpenSSL resolved executable hashes are recorded |

Created the venv with the exact base interpreter's `-m venv --copies --without-pip`;
installed dependencies from the existing offline uv cache. No dependency download occurred.
The preliminary `uv venv --copies` command was unsupported and created no environment;
the subsequent native Python venv creation succeeded.

[Sanitized preparation evidence](hm2-issuer-preparation-evidence.json) matches the local
manifest. Source copies and the four previously tested runtime/test hashes match their
published evidence. New installation checks passed: imports/issuer CLI help, disabled
renewer exit, copied age versions, all ten packages compatible, native Security framework
symbols available, and configured Keychain/SSH paths exist. No native Keychain function
was called, no key contents were read, and no unchanged test suite was rerun.

The venv is stable across checkout changes, but still depends on its exact base runtime.
Do not prune that runtime or replace the interpreter/dependencies in place after enrollment.
Review upgrades separately and re-prove native access. The native helper disables user
interaction and uses the creator's default item access control; symbol availability does
not prove access rights. If access is denied, stop for Steve to authorize the exact
interpreter locally in Keychain Access. Never broaden to all applications or collect a password.

## Approved operational scope

One bounded enrollment/recovery operation, with the following concrete effects:

1. Add **one CA** to `/Users/steve/Library/Keychains/login.keychain-db`, generic-password
   service **`modelmatch/home-server/issuer-v1`**, account **`steve`**. Preserve the existing
   `modelmatch/home-server/recovery-key-v1` item. Use the merged RSA-3072, SHA-256,
   730-day, `CA:true,pathlen:0`, `keyCertSign,cRLSign` contract with exact CN
   `driftplain-home-server-issuer-v1`. No leaf certificates in this gate.
2. Create public `issuer.crt`, `issuer.sha256`, `ledger.json` and `ledger.lock` beneath
   the operator root. Independently verify the certificate with OpenSSL, compare its
   lowercase DER SHA-256 fingerprint with the issuer output, then pin `renewal.json`.
   Run enrollment again in a fresh noninteractive process with stdin closed, using the
   same stable interpreter; require the same fingerprint and ledger digest.
3. Create the schema-2 age-encrypted CA/certificate/complete-ledger bundle beneath
   `encrypted-backups/` and upload through profile **`saa`**, operator
   **`arn:aws:iam::957261948820:user/steve`**, to bucket
   **`modelmatch-home-server-backups-957261948820`**, **`ap-south-1`**, under
   **`recovery/issuer-v1/<bundle-digest>.age`**. Require exact version readback, preserve
   its receipt and all ciphertext. Ordinary S3 storage/requests and recovery reads apply;
   no new paid service, expiry policy or secret is introduced. A lost upload response
   may require another version under the same key; retain and reconcile all versions.
4. Preserve the public checkpoint on the home server at
   **`/home/steve/.local/share/driftplain/home-server-issuer-checkpoints/<checkpoint-id>/`**,
   owned by `steve`, mode `0700`, files `0600`. Use a unique UTC timestamp plus ledger
   digest for the checkpoint ID; create exclusively and never overwrite a differing
   existing checkpoint. This is a public-file transfer, not a runtime/home helper install.
5. Retrieve that checkpoint from home into a fresh local recovery input directory,
   then run `verify-recovery --recovery-source aws`: retrieve the exact S3 version and
   read the **existing** Secrets Manager secret `modelmatch/home-server/recovery-key-v1`
   through the operator helper. Decrypt in memory and validate the complete bundle.
   No read of either local private Keychain item is needed by this recovery command.
6. Save sanitized enrollment, upload, independent recovery and checkpoint readback
   evidence locally and prepare reviewed source evidence. Stop before commits and
   before the subsequent identity plan/apply gates.

This approval excludes `restore` (which adds real replacement custody), initial leaf
issuance, CRL generation/import, IAM/identity creation or session enablement, scheduler
installation, home application/Secret changes, production export, public routing and
teardown. It also excludes remote Git publication. AWS production and `DRY_RUN=1` remain.
The identity root's creation/session flags remain false. No paid LLM calls.

## Independent checkpoint and recovery acceptance

**Approved checkpoint authority for loss of this Mac: the separate home server.**
Before the upload, compute the canonical public-ledger digest directly from `ledger.json`
using sorted JSON keys and compact separators, and save that digest with the independently
checked CA fingerprint. Do not obtain the expected digest from the decrypted archive.
With renewal disabled and no concurrent issuance, require that digest to equal the upload
receipt's `ledger_sha256` and the still-current original ledger after backup.

Each uniquely named checkpoint contains only an explicit allowlist:

- `issuer.crt`, `issuer.sha256`, complete public `ledger.json`, `receipt.json`;
- `checkpoint.json`: checkpoint ID/time, independently computed CA/ledger digests,
  issued/pending/revoked counts, CRL number, exact S3 key/version/ciphertext hash,
  recovery-key ID/recipient and the source revision;
- `SHA256SUMS` over those files, plus the public preparation manifest and exact dependency
  lock, public recipient and OpenSSL verification output.

Transfer only those public files over the handoff's strict-key SSH command to
`steve@192.168.1.93`. No CA/recovery private key, AWS credentials or encrypted bundle goes
to home. Verify remote file hashes, then fetch a new copy from home and verify it again.
The receipt/checkpoint is not publicly served. The home directory was checked for symlinks
and existing history, created under the approved scope, and verified by a separate fetch.
Continue to reject symlinks or unexpected existing content before subsequent use.

For later transactions, retain a new checkpoint after **every** issuance/revocation/CRL
or other ledger change. A transaction is not recovery-complete until its updated checkpoint
has been copied and read back. After Mac loss, inspect these checkpoint histories plus
S3 `ListObjectVersions` restricted to `recovery/issuer-v1/`; reconcile any later or unmatched
archive before selecting a complete checkpoint. A valid old receipt or a highest timestamp
alone is not freshness proof. Never trust an unverified mutable `latest` pointer.

This covers Mac loss while the home checkpoint and operator AWS recovery access survive.
Home is independent of the Mac, but a compromised home host can alter its checkpoint copy;
hashes detect transfer corruption, not malicious replacement. Cross-check reviewed evidence
and S3 history and stop on conflicts. Simultaneous Mac/home loss or compromise requires
another independently retained checkpoint; no such additional custody is claimed here.
AWS account/MFA recovery remains Steve's responsibility. Do not claim an account-recovery
drill occurred merely because the existing Mac's operator profile can read AWS.

The staged `recovery-drill.json` directs the verifier to:

- `recovery-drill/unavailable/login.keychain-db` (nonexistent);
- `recovery-drill/state/issuer.crt` and `recovery-drill/state/ledger.json` (nonexistent);
- `recovery-drill/unavailable/encrypted-backups` (nonexistent).

After approved enrollment, pin its `ca_sha256` from the independent checkpoint. Select
the receipt and expected ledger hash from the copy fetched from home. Confirm these
isolated issuer paths are absent before and after verification; only the verifier's
isolated parent directory/lock may be created. Leave the original issuer custody/files
intact. This demonstrates recovery validation without using them, not a replacement-Mac
Keychain restore. Operational `restore` retains its own approval gate.

Operator sequence, executed only after the separate operational approval:

```bash
source /Users/steve/.local/share/driftplain/home-server-identity/operator.env
"$HOME_SERVER_PYTHON" "$HOME_SERVER_ROOT/home-server-issuer.py" enroll --config "$HOME_SERVER_CONFIG"
/opt/homebrew/bin/openssl x509 -in "$HOME_SERVER_CA_FILE" -noout -text -fingerprint -sha256
/opt/homebrew/bin/openssl verify -CAfile "$HOME_SERVER_CA_FILE" "$HOME_SERVER_CA_FILE"
# Compare/pin public fingerprint, then repeat enrollment with </dev/null.
# Record independent ledger checkpoint before backup.
"$HOME_SERVER_PYTHON" "$HOME_SERVER_ROOT/home-server-issuer.py" backup --config "$HOME_SERVER_CONFIG"
# Transfer/read back the public checkpoint; set these from that independent copy:
# HOME_SERVER_RECEIPT = fetched receipt.json (not the enclosing backup result JSON)
# HOME_SERVER_LEDGER_SHA256 = independently selected checkpoint's canonical ledger digest
"$HOME_SERVER_PYTHON" "$HOME_SERVER_ROOT/home-server-issuer.py" verify-recovery \
  --config "$HOME_SERVER_RECOVERY_CONFIG" --receipt "$HOME_SERVER_RECEIPT" \
  --expected-ledger-sha256 "$HOME_SERVER_LEDGER_SHA256" --recovery-source aws
```

Before any operational invocation, recheck installed hashes/config, operator identity,
bucket versioning/no expiry and the secret's public metadata. Do not use a saved old
Terraform plan. A custody failure with a surviving journal stops for review; never delete
the journal, reset the fingerprint or regenerate keys to bypass it.

The preparation manifest is historical: both config fingerprints were placeholders then.
Runtime hashes still match it; current pinned config hashes are in the operational
evidence's `postchecks.config_sha256`. Do not restore the placeholder or change an issuer
pin to make a preparation-time hash comparison pass.

## Verified result — September 13, 2026

- Native enrollment and fresh noninteractive readback passed with the same CA and ledger.
  OpenSSL independently verified the public certificate and its SHA-256 fingerprint:
  `b125437b857bf35561dd93e589b477d7284fce7e457a953ff83db859839b00b4`.
  Validity ends **September 12, 2028 at 01:38:19 UTC**. Reviewable public copies are
  [home-server-issuer.crt](home-server-issuer.crt) and [fingerprint](home-server-issuer.sha256).
- One **6,378-byte** encrypted bundle was uploaded and version-readback verified at
  `recovery/issuer-v1/b034b0e2cbeed365e81e866fac143d12dbdc7f6ab303aa17ee2c968b80ca1ced.age`,
  S3 version **`kmhfg7PTVEoi6rAehXkepa2dbnmq8_pn`** in the selected bucket.
- Ten public checkpoint files were saved and independently retrieved from
  `/home/steve/.local/share/driftplain/home-server-issuer-checkpoints/20260913T014016Z-e3f61fb7ca3305f2b6446408295d754e68438de39747fefb59cbb0d43d9201ed/`.
  Remote ownership/modes and both remote/local hashes passed. All issuer S3 version history
  reconciled to this one archive; there were no prior versions or delete markers.
- AWS-key recovery verification passed at **01:41:45 UTC**, using that retrieved receipt
  and independently selected complete-ledger digest. The isolated issuer Keychain,
  certificate, ledger and archive paths remained absent; only the isolated lock appeared.
  The original ledger is unchanged. Neither local recovery-key access nor native replacement
  restoration was needed. Existing AWS recovery-key version
  `abd5ad3c-3ff5-5d47-91b0-aff63b2df616` was preserved.
- Fresh production `/readyz` returned ready/db ok; budget Lambda still has `DRY_RUN=1`.
  Both configs remain disabled, no renewal job is registered, and identity creation/session
  source flags remain false. No IAM/CRL, leaf, paid LLM, commit or publication action ran.

Acceptance for this gate is a verified CA, noninteractive native readback, version-pinned
S3 upload, independently retrieved checkpoint and successful complete **empty-ledger**
recovery validation. It does not prove later issuance/revocation history recovery; repeat
that gate after initial leaves are recorded. Native restoration, full-history acceptance,
disabled-first identity provisioning, live IAM/CRL proof and automatic renewal/alerts
remain subsequent work. Historical EKS/IRSA/daily-destroy lesson rules are overridden by
the approved E21 home direction; no AWS production policy is changed by this preparation.
