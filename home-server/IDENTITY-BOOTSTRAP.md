# HM2 initial identity bootstrap — September 15, 2026

**Prepared and locally tested; source review and operational approval pending.**
No operational leaf, namespace, Secret, CRL, AWS session or scheduler was created.
The ten focused tests use disposable crypto, fake remote/AWS boundaries and forbidden
network/Keychain access. They include a real local age round trip of the new bootstrap
history. This does not replace the required operational populated-ledger recovery.

## Exact proposed operation

Use the enrolled CA and stable operator runtime at
`/Users/steve/.local/share/driftplain/home-server-identity/` with its original disabled
`renewal.json`, ledger lock, Keychain items and age recipient. Do not rebuild/re-enroll.
September 15 public-only readback confirms no issued/pending/revoked leaves, CRL number 0,
and unchanged canonical ledger SHA-256
`e3f61fb7ca3305f2b6446408295d754e68438de39747fefb59cbb0d43d9201ed`.
Enrolled CA DER SHA-256:
`b125437b857bf35561dd93e589b477d7284fce7e457a953ff83db859839b00b4`.

| Identity | Namespace / operator-managed Secret | CN |
|---|---|---|
| Backup | `home-server-backups/home-server-backup-identity` | `driftplain-home-server-backup` |
| Bedrock | `app/home-server-bedrock-identity` | `driftplain-home-server-bedrock` |

Names match [the existing renewal helper](home-server-leaf.py). September 15 read-only
home inventory found only default/system namespaces: both proposed namespaces are absent.
Bootstrap creates only these namespaces and two TLS Secrets. No workload mounts them yet.
HM4 must reference these exact names and delegate the existing certificate annotation on
`app/modelmatch-backend` before installing renewal. Do not invent that Deployment in HM2.

New source: [Mac bootstrap](home-server-bootstrap.py) and
[root-only home bootstrap](home-server-bootstrap-leaf.py). Existing issuer, credential
wrapper and renewal files are unchanged. Bootstrap is explicit, requires a disabled
renewal config and cannot replace existing identities. It does not install a schedule or
enable AWS authentication.

After source review, propose this separate operational scope:

1. Fresh preflight: inspect current Git/source hashes, operator account, exact disabled
   anchor/profiles from [applied evidence](hm2-identity-applied-evidence.json), absence of
   pending issuer work/identity Secrets, and original CA/config pins. Confirm the current
   `home-server` SSH alias and strict host-key binding from [remote access](REMOTE-ACCESS.md).
   Stop on conflicting state. Keep `DRY_RUN=1` and do not run an old Terraform plan.
2. Add the reviewed Mac bootstrap file alongside the existing stable scripts, preserving
   interpreter/dependencies/config/custody. Install only `home-server-bootstrap-leaf.py`
   and its unchanged `home-server-leaf.py` dependency under root-owned `/opt/home-server/`
   on Ubuntu. Copy the public CA to `/var/lib/driftplain/home-server-identity/issuer.crt`;
   compare its DER fingerprint. Use `sudo -n`, root-owned code and a mode-0700 state root.
   Create the two absent namespaces using explicit K3s context/server arguments. No ArgoCD
   root, app, DB, helper binary, API permission or scheduler is part of this operation.
3. Run explicit Mac bootstrap once per identity using the stable interpreter/config.
   Home generates an RSA-3072 private key under its root-only staging directory and returns
   only a CSR. Mac validates/signs it for 90 days, records the serial and pending transaction,
   then encrypts/verifies the complete issuer bundle in S3 **before** installing the Secret.
   Home verifies issuer, subject and key pair; uses atomic `create` via stdin, verifies the
   Secret key/cert readback and removes only that transaction's staging files. A concurrent
   creator or different existing Secret stops. No Deployment patch/rollout occurs.

   ```bash
   "$HOME_SERVER_PYTHON" "$HOME_SERVER_OPERATOR_ROOT/home-server-bootstrap.py" \
     --config "$HOME_SERVER_OPERATOR_ROOT/renewal.json" --identity backup
   "$HOME_SERVER_PYTHON" "$HOME_SERVER_OPERATOR_ROOT/home-server-bootstrap.py" \
     --config "$HOME_SERVER_OPERATOR_ROOT/renewal.json" --identity bedrock
   ```

   Here `HOME_SERVER_OPERATOR_ROOT` is the exact stable directory above and
   `HOME_SERVER_PYTHON` is its `venv/bin/python3`; use its existing `operator.env` safeguards
   and pinned age PATH. Commands emit public certificate metadata and recovery receipts only.
4. Preserve all issued/pending history on failure. Retry the same transaction: failed S3
   upload prevents Secret creation; a lost install response reconciles the same certificate;
   a failed final backup retries without reissuing. Bootstrap leaves `backup_required=true`
   conservatively so the final receipt matches the exact saved ledger. The existing renewer
   understands that flag. Never clear history/delete a Secret to force a retry.
5. After both leaves, copy the final public receipt, complete public ledger, CA and checksum
   to a unique timestamp/digest checkpoint under the existing home checkpoint directory.
   Retrieve it independently into a new Mac checkpoint directory and compare hashes. Keep
   Mac ciphertext and exact S3 versions. Use the existing isolated recovery config and AWS
   recovery-key path to run `verify-recovery` against this independently selected receipt
   and ledger digest, without reading the original issuer custody/files. Compare every
   issued/pending/revoked entry, counts, serials and signing settings. No replacement-Mac
   Keychain restoration is needed for this bounded verification.

Acceptance: two matching Secret/key pairs with the expected public subjects; full journal
and final encrypted archive readback; independent complete populated-ledger recovery;
unchanged disabled AWS authentication and renewal/scheduler state. Record exact times,
serials, expiries, source hashes, S3 version/checkpoint and postcheck results. Ordinary
S3 upload/read costs apply; no paid LLM request is included. This operation has not run.

## Subsequent separate gates

| Gate | Concrete acceptance and boundary |
|---|---|
| CRL | Separately approve initial full CRL creation/backup and enabled AWS import against anchor `913f6b1b-d09a-41be-8715-e41e04ecda90`; retain returned CRL ID, compare exact public readback using `verify-crl`; do not enable anchor/profiles yet |
| Authentication | Prepare a fresh complete Terraform enablement plan and bounded disposable-leaf procedure, then obtain approval; prove valid matching roles, cross-role/profile rejection, wrong-anchor rejection and fresh revoked-leaf rejection. Credentials stay in pipes; STS checks use no LLM tokens |
| Disposable revocation witness | Requires a separately recorded extra test leaf and full-history backup, never accidental revocation of one of the two operational leaves. The initial bootstrap command intentionally cannot issue extra leaves after initialization. Prepare that bounded issuance/retirement tooling before requesting its operational approval |
| Existing-session containment | Preserve [the incident procedure](ISSUER.md#compromise-containment-and-live-acceptance). A previously allowed tiny backup PUT is the live denial witness after an approved temporary deny. Bedrock uses installed-policy evaluation and the one-hour expiry boundary unless paid-call-risk approval is separately given; `GetCallerIdentity` does not prove denial |
| HM4 | Reviewed helper/image and real SDK exchange/refresh; home app/Secret mounts; GitOps annotation ownership and sync survival. Names above are a contract, not installed workloads |
| HM5 | Renewal/CRL refresh/backup schedules; independent expiry/heartbeat/availability alerts with tested delivery. Mac renewal currently uses LAN SSH; any away-renewal transport change needs its own verification. Bootstrap's use of the verified SSH alias does not modify renewal |

Do not mark HM2 accepted merely because bootstrap is prepared. CRL and authentication
remain required identity evidence under the handoff; actual workload integration and
installed recurring schedules stay in HM4/HM5. HM3 production export/restore is unstarted.
