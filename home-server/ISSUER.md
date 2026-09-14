# HM2 issuer enrollment, recovery and revocation

September 13, 2026. **Source merged in PR #21. Operational CA enrollment and independent
empty-ledger recovery verified after separate approval; no leaf/identity/scheduler deployed.**
The selected Roles Anywhere/Keychain/automatic leaf-renewal design is unchanged.
[home-server-issuer.py](home-server-issuer.py) adds operator commands and shares validation
with [the renewer](home-server-renew.py). It performs no IAM/CRL API writes, SSH, leaf
installation or scheduler installation. `backup`, `crl` and `revoke` do upload issuer
ciphertext to S3 when explicitly invoked. `enroll` and `restore` can add the Keychain item.
Those operational invocations require the handoff's separate approval.

**Operational preparation — September 13:** the merged scripts, stable interpreter and
disabled configs are now staged on the Mac. [Concrete enrollment approval package](ISSUER-ENROLLMENT.md)
records exact paths, the approved independent home-held public checkpoint and the bounded
recovery drill. [Operational evidence](hm2-issuer-enrollment-evidence.json) records native
readback, exact S3-version verification and independent recovery success. Steve approved
this operational record and its PR/merge publication on September 14; later
IAM/CRL/leaf/scheduler gates remain.

## Custody and transaction rules

- CA: RSA-3072, exponent 65537, SHA-256, self-signed X.509v3, 730 days plus five-minute
  validity backdating; critical `CA:true,pathlen:0` and only `keyCertSign,cRLSign` usages.
  Exact subject/issuer CN: `driftplain-home-server-issuer-v1`.
- Private CA key: canonical unencrypted PKCS8 PEM **inside the local login Keychain**,
  service `modelmatch/home-server/issuer-v1`, account `steve`. Native APIs have no password
  prompts or overwrite operation. No PEM file, clipboard, argv, environment or log output.
  The age helper's value-specific `get/add` methods are never used for PEM.
- Public state: `issuer.crt`, `issuer.sha256`, and `ledger.json` in the configured operator
  directory. Every issuer/renewal command takes the same exclusive `ledger.lock`.
  The ledger retains the exact public CA, signing settings, every issued certificate,
  pending delivery, revocation date/reason, and monotonically increasing CRL number.
  Certificate PEM preserves each serial, subject, public key and validity interval;
  previous-serial/pending records describe replacement progress.
- Enrollment journals the public CA/empty ledger **before** adding its key. A lost response
  after Keychain creation retries the same key. Missing public certificate/fingerprint
  files are repaired from the retained ledger after key-pair verification.
  Missing key with surviving state, configured fingerprint or encrypted archives stops.
  Missing ledger with a surviving key also stops; no empty history is invented.
- If native add definitely failed before storing the key, the journal alone cannot recover
  that private key. Keep the evidence and review an explicit reset of this never-used
  enrollment attempt; the tool deliberately has no automatic reset/delete command.
- Bundles use schema **2**. The previous schema 1 existed only in disposable tests; no
  operational issuer exists to migrate. A version mismatch fails rather than silently
  dropping fields. Preserve historical encrypted files; there is no bundle cleanup.
- Restore fills missing files/items or accepts exact matches. Any differing surviving
  ledger/key/certificate stops, including operational metadata changes after a backup.
  Reconcile the newest history first; do not delete newer files to force an old restore.

## Review and operational enrollment sequence

These are **future operator commands**, not actions performed during preparation. Run
from the canonical infra checkout with a reviewed stable Python 3.12 environment containing
`boto3==1.43.24`, `botocore==1.43.24`, `cryptography==50.0.1`, plus `age` and `age-keygen`.
Set `HOME_SERVER_PYTHON` to that environment's absolute Python executable and
`HOME_SERVER_CONFIG` to the operator copy of
[home-server-renewal.example.json](home-server-renewal.example.json).
Keep `enabled=false`; that field controls scheduled renewal, not explicit issuer commands.
Do not use disposable `uv --with` environments for operational Keychain enrollment.

1. Review this source/tests and approve commits separately. Review operational enrollment
   separately, including the exact keychain/config paths and fingerprint output location.
2. After enrollment approval, run the command below. Compare the public certificate using
   OpenSSL and place its lowercase SHA-256 fingerprint in the operator config's `ca_sha256`.
   Preserve this independently reviewed fingerprint in the enrollment evidence.

   ```bash
   "$HOME_SERVER_PYTHON" home-server/home-server-issuer.py enroll --config "$HOME_SERVER_CONFIG"
   openssl x509 -in "$HOME_SERVER_CA_FILE" -noout -subject -issuer -serial -dates -fingerprint -sha256
   openssl verify -CAfile "$HOME_SERVER_CA_FILE" "$HOME_SERVER_CA_FILE"
   ```

   `HOME_SERVER_CA_FILE` is the config's `ca_certificate`, not a private-key file.
   Enrollment emits only the fingerprint, ledger digest and public counts. A second
   invocation is idempotent. Authorize the chosen Python executable locally in Keychain
   Access if needed; prove a later noninteractive invocation, never share a password.
3. With explicit S3-upload approval, run `backup`. It encrypts the entire key/certificate/
   ledger/signing configuration to the existing recovery recipient, saves ciphertext on
   the Mac, uploads to `recovery/issuer-v1/`, and compares an exact S3 version readback.

   ```bash
   "$HOME_SERVER_PYTHON" home-server/home-server-issuer.py backup --config "$HOME_SERVER_CONFIG"
   ```

   Preserve the emitted public receipt separately from the lost-Mac failure domain:
   bucket/key/version, ciphertext SHA-256, CA fingerprint, canonical ledger SHA-256,
   recovery-key ID/recipient and verification time. The matching receipt also lives beside
   the Mac ciphertext. Copy public receipt/checkpoint evidence into the reviewed operational
   record after each issuance/revocation transaction. S3 byte readback proves upload integrity,
   **not** private-key recovery. `backup_required` may remain true in an operator snapshot;
   this conservative flag can cause an additional verified upload during renewal.
4. Perform the independent recovery procedure below while authentication is still disabled.
   Validate the full ledger, not just an age challenge. Retain issued/revoked history through
   every later restore. Recovery must pass again after the first populated ledger exists.
5. Only then review a fresh identity Terraform plan with creation on and sessions off.
   Review runtime attribute mapping and live negative tests before enabling authentication.
   No Terraform variable, flag or resource is changed by this slice.

## Independent recovery and a lost Mac

An encrypted old bundle can be internally valid while missing newer revocations. Therefore
the operator must select the **latest verified complete checkpoint** from independently
retained receipts/issuance/incident evidence and, after a Mac loss, inspect S3 object versions
with the operator identity. A hash supplied by the same unverified old bundle is not a
freshness proof. There is no automatically trusted `latest` pointer.

Set `HOME_SERVER_RECEIPT` to the selected public receipt JSON and
`HOME_SERVER_LEDGER_SHA256` to its independently confirmed complete ledger checkpoint.
On a trusted replacement Mac, use the same pinned public CA/config and run:

```bash
"$HOME_SERVER_PYTHON" home-server/home-server-issuer.py verify-recovery \
  --config "$HOME_SERVER_CONFIG" --receipt "$HOME_SERVER_RECEIPT" \
  --expected-ledger-sha256 "$HOME_SERVER_LEDGER_SHA256" --recovery-source aws
```

This reads the exact S3 version, obtains the existing age identity through the operator's
Secrets Manager path, decrypts only into memory, and validates CA signature/constraints/
validity, private/public pairing, independently pinned fingerprint, signing settings and
**all** issued/pending/revoked entries. It never reads the original issuer Keychain item
or original ledger/public files. `--recovery-source keychain` uses the existing local age
item instead; this command still downloads ciphertext from S3. No new recovery key is made.
An expired historical CRL can be recovered but must be refreshed before import. An expired
CA is rejected for operational restoration; retain its archives for historical custody.

After separately approved restoration, repeat with command `restore` and the same arguments.
This adds only a missing CA Keychain item and missing public state, verifies native readback,
and preserves the exact recovered ledger. Repeat is idempotent. Run the public OpenSSL
checks again, then approve/test noninteractive access on the replacement interpreter.
Keep leaf renewal disabled until host state/pending deliveries are reconciled. If the Mac
or CA was compromised, recovering that CA does not make it trustworthy: replace the issuer
and trust anchor under incident review instead.

## Signed CRLs and AWS import/update

A **CRL** is a CA-signed list of revoked certificate serials. Roles Anywhere requires an
imported CRL; it does not call certificate distribution-point URLs or OCSP.
[AWS revocation behavior](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/trust-model.html#revocation).

The prepared policy uses a 35-day `nextUpdate` (bounded by CA expiry), monthly refresh and
immediate refresh after compromise. Before production, HM5 must automate or independently
alert on the refresh deadline with tested delivery; leaf renewal does not refresh CRLs.
Warn when seven days remain, escalate at one day, and treat expiry as a failed operating
gate. Do not assume AWS's treatment of an expired CRL is an emergency containment control.

After operational approval, `crl` creates a full list (including an initial empty list).
`revoke --serial DECIMAL_SERIAL` records an issued serial as compromised. Unknown serials
fail. Repeat revocation retains the original date and never duplicates the entry. CRL
numbers increase on each generation, including retries; old CRLs/bundles are retained.

```bash
"$HOME_SERVER_PYTHON" home-server/home-server-issuer.py crl --config "$HOME_SERVER_CONFIG"
"$HOME_SERVER_PYTHON" home-server/home-server-issuer.py revoke \
  --config "$HOME_SERVER_CONFIG" --serial "$HOME_SERVER_REVOKED_SERIAL"
```

Revocation removes a matching pending delivery while retaining issued history; ordinary
renewal refuses a revoked current or pending leaf and any current certificate absent from
the ledger. Initial leaf bootstrap must record its certificates before renewal is installed.
The command journals the signed CRL,
backs up the full state, then publishes public `issuer-crl-N.pem` beside the ledger.
If S3 fails, the revocation remains journaled, the command fails, and no new import file
is published. Keep emergency session denies in place; retry `crl` to back up/publish the
complete list. **Generating a CRL alone does not revoke anything in AWS.**

For a separately approved import/update, obtain the exact anchor ARN/ID and workload role/
profile IDs from the reviewed identity root outputs. Use account `957261948820`, region
`ap-south-1`, operator profile `saa`. Confirm the anchor's certificate fingerprint matches
the enrolled CA. `HOME_SERVER_CRL_FILE` is the latest generated public PEM file.

```bash
aws --profile saa --region ap-south-1 rolesanywhere get-trust-anchor \
  --trust-anchor-id "$HOME_SERVER_ANCHOR_ID" > "$HOME_SERVER_ANCHOR_RECORD"

# First import only; retain the returned crlId. --crl-data is a binary CLI field
# containing PEM bytes. Import takes the trust-anchor ARN, not its bare ID.
aws --profile saa --region ap-south-1 rolesanywhere import-crl \
  --name modelmatch-home-server-issuer-v1 --trust-anchor-arn "$HOME_SERVER_ANCHOR_ARN" \
  --crl-data "fileb://$HOME_SERVER_CRL_FILE" --enabled

# Subsequent refresh; update has no --enabled argument.
aws --profile saa --region ap-south-1 rolesanywhere update-crl \
  --crl-id "$HOME_SERVER_CRL_ID" --crl-data "fileb://$HOME_SERVER_CRL_FILE"
aws --profile saa --region ap-south-1 rolesanywhere enable-crl --crl-id "$HOME_SERVER_CRL_ID"
aws --profile saa --region ap-south-1 rolesanywhere get-crl \
  --crl-id "$HOME_SERVER_CRL_ID" > "$HOME_SERVER_CRL_RECORD"

"$HOME_SERVER_PYTHON" home-server/home-server-issuer.py verify-crl --config "$HOME_SERVER_CONFIG" \
  --anchor-record "$HOME_SERVER_ANCHOR_RECORD" --crl-record "$HOME_SERVER_CRL_RECORD" \
  --anchor-arn "$HOME_SERVER_ANCHOR_ARN" --crl-id "$HOME_SERVER_CRL_ID"
```

The readback files contain only public metadata/certificates. `verify-crl` checks the exact
account/region/anchor, enrolled CA fingerprint, CRL ID, enabled flag, exact current PEM,
signature, serial set, sequence and validity. It performs no cloud call or Keychain read.
It permits an intentionally disabled trust anchor during staging. Re-fetch both records
after changes; saved readback is point-in-time evidence, not monitoring or IAM enforcement.
If import's response is lost, inspect `list-crls` for the anchor/name and verify `get-crl`
before retrying; do not create duplicate imports blindly.
[Import CLI](https://docs.aws.amazon.com/cli/latest/reference/rolesanywhere/import-crl.html),
[update CLI](https://docs.aws.amazon.com/cli/latest/reference/rolesanywhere/update-crl.html),
[enable API](https://docs.aws.amazon.com/rolesanywhere/latest/APIReference/API_EnableCrl.html).

## Compromise containment and live acceptance

After applicable incident approval, disable affected profiles (both for a lost home host),
or the whole trust anchor for CA compromise. Attach the temporary deny below as an inline
policy only to the affected roles, `modelmatch-home-server-backup` and/or
`modelmatch-home-server-bedrock`. Save it as a reviewed public policy file before invoking
`aws iam put-role-policy --role-name ROLE --policy-name home-server-incident-deny
--policy-document file://FILE` with the same operator profile. This operation is not run
or automated here.

```json
{"Version":"2012-10-17","Statement":[{"Sid":"HomeServerIncidentContainment","Effect":"Deny","Action":"*","Resource":"*"}]}
```

Disabling the anchor/profile or revoking a leaf prevents new exchanges but does not by
itself invalidate already issued sessions. An unconditional role deny contains existing
and newly issued sessions during the incident; IAM propagation still needs verification.
AWS's `AWSRevokeOlderSessions` policy is the alternative cutoff-time mechanism. Before
lifting the unconditional deny, invalidate every session from before the containment
cutoff with that mechanism, or retain containment until all possible one-hour sessions
have expired after verified exchange shutdown. Otherwise previously stolen credentials
could regain permission. Reconcile emergency changes into reviewed configuration before
Terraform can undo them. [AWS role-session revocation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_revoke-sessions.html).

Then revoke affected serials, refresh/import/verify the CRL, and rebuild trusted host keys.
CA compromise requires a new issuer/anchor, not just a CRL from the compromised key.
Keep history and ciphertext. Removal of the deny and re-enabling authentication require
separate incident recovery review and the following observed checks:

- Before revocation, a disposable valid leaf obtains its matching role session. Wrong
  role, profile and anchor fail. Capture only pass/fail and public identifiers/times;
  credentials remain in a process pipe, never terminal output.
- After enabled CRL import, that still-unexpired revoked leaf must fail a **fresh**
  `CreateSession`; an unrevoked fixture must still obtain the intended role. Avoid cached
  credentials, which do not test certificate revocation. Independently verify wrong CA,
  disabled CRL, stale CRL data and expired nextUpdate are rejected by local readback checks.
- A session acquired before containment must lose a previously allowed permission after
  the deny propagates. `sts:GetCallerIdentity` is **not** a permission-denial witness.
  For backup, use an approved tiny write in its permitted recovery prefix. For Bedrock,
  retain the deny and use IAM policy evaluation plus the session-expiry boundary;
  live InvokeModel denial needs an explicitly approved paid-call risk if enforcement
  unexpectedly fails. Do not claim live Bedrock enforcement without that evidence.
- Record the propagation timeline; keep containment if results are ambiguous. Only after
  safe session expiration/revocation and fresh-key verification review service restoration.

## Local evidence and remaining gates

Run the focused suites with disposable crypto and fake Keychain/AWS/SSH:

```bash
uv run --offline --no-project --python 3.12 --with boto3==1.43.24 --with botocore==1.43.24 \
  --with cryptography==50.0.1 python home-server/test-home-server-issuer.py
uv run --offline --no-project --python 3.12 --with boto3==1.43.24 --with botocore==1.43.24 \
  --with cryptography==50.0.1 python home-server/test-home-server-renewal.py
```

[Sanitized evidence](hm2-issuer-local-evidence.json) records the exact tested source hashes.
Native noninteractive Keychain access and operational independent empty-ledger recovery
now pass; see the separate operational record above. Full-history recovery after initial
leaves, replacement-Mac native restoration, disabled-first cloud plan, live role/anchor/CRL
proof, home GitOps Secret/annotation ownership, runtime helper image, scheduled leaf renewal
and independent monitoring remain gates.
Install this new issuer module alongside the renewer and custody helper in the eventual
stable runtime; a source merge alone does not install it. Existing age custody, AWS
production, budget `DRY_RUN=1`, application identifiers and paid-call restrictions remain.
