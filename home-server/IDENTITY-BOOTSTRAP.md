# HM2 initial identity bootstrap — September 15, 2026

**Operational bootstrap approved and verified September 15; new evidence source review pending.**
Source was approved and merged through infra PR #25. Steve separately approved helper
installation, the two namespaces/leaf Secrets, encrypted backup and independent recovery.
[Applied evidence](hm2-bootstrap-applied-evidence.json) records the result below.
No CRL, AWS session or scheduler was created.
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

The separately approved operational scope was:

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
S3 upload/read costs apply; no paid LLM request is included.

## Verified operational result

Executed September 15 with the merged, hash-verified code and original stable runtime.
Native CA readback succeeded without interaction. The original CA and age custody,
disabled configs and existing issuer/renewal scripts were preserved. Only the new Mac
bootstrap command, two root-owned home scripts/public CA and the two namespaces were added.

| Identity | Certificate serial | Expiry (UTC) |
|---|---|---|
| Backup | `188550183090849239859899631588670510961450592200` | December 14, 2026, 13:02:39 |
| Bedrock | `308719772415312223547387245572677494936844969648` | December 14, 2026, 13:02:57 |

Both root-only home key-pair checks and Mac public certificate signature/ledger comparisons
passed. The staging keys/CSRs/candidate certificates were removed after verified installation;
root-owned mode-0700 identity directories retain only their locks. Neither namespace has
an app/backup Pod, Deployment, StatefulSet, Job or CronJob.

Final complete ledger SHA-256:
`07671c3b9827534cad840a763aa41029a1387f543c63930d8ae4fe4dbf6a5cac`.
It contains **two issued leaves, zero pending deliveries, zero revocations, CRL number 0**.
The conservative `backup_required=true` flag is preserved so the receipt matches the exact
ledger; it does not indicate a failed upload. Existing renewal can reconcile that flag
when its later installation is approved. No repeated bootstrap/test/drill was needed.

Final encrypted archive: **9,782 bytes**, bucket `modelmatch-home-server-backups-957261948820`,
key `recovery/issuer-v1/9e0b54c5ef01d6a3d813cdeb8eaad35df844802a345fb6040c1eb37c391982e3.age`,
version `Kv3GT32UtyEOt0rB51FgVGGfXDYljWur`; ciphertext SHA-256
`2589d8a3e1b433519d5e00aa9e7e1b427f2886247d2e6b23cf5a1c4a6bfa1156`.
Exact-version S3 readback passed, and all prior ciphertext/receipts were retained.

Independent public checkpoint on home:
`/home/steve/.local/share/driftplain/home-server-issuer-checkpoints/20260915T130406Z-07671c3b9827534cad840a763aa41029a1387f543c63930d8ae4fe4dbf6a5cac/`.
Separate retrieval at **13:04:09 UTC** matched every public-file hash. The retrieved
receipt/digest then drove `verify-recovery --recovery-source aws` using the existing
isolated config and AWS recovery-key version. Recovery validated the original CA/key,
signing settings and exact complete populated ledger without the original issuer
Keychain item or original files. The isolated original paths remained absent; no native
replacement-Mac restoration was performed. This proves initial two-leaf history recovery,
not a live revoked-leaf history or production DB restore.

Postchecks at **13:05:24 UTC** confirm anchor/profiles disabled, no imported CRL, renewal
configs disabled and launchd job absent; original recovery-key version and `DRY_RUN=1`
are preserved. Normal renewal becomes due November 14, 2026; HM5 must install and verify
the schedule/independent monitor before production use. Raw operator receipts remain under
`/Users/steve/.local/share/driftplain/home-server-identity/bootstrap-20260915/`.

## Subsequent separate gates

| Gate | Concrete acceptance and boundary |
|---|---|
| CRL | Completed during continued HM2 acceptance: nonempty full CRL creation/backup and enabled AWS import against anchor `913f6b1b-d09a-41be-8715-e41e04ecda90`; retain returned CRL ID, compare exact public readback using `verify-crl`; do not enable anchor/profiles yet |
| Authentication | Prepare a fresh complete Terraform enablement plan and bounded disposable-leaf procedure, then obtain approval; prove valid matching roles, cross-role/profile rejection, wrong-anchor rejection and fresh revoked-leaf rejection. Credentials stay in pipes; STS checks use no LLM tokens |
| Disposable revocation witness | Requires a separately recorded extra test leaf and full-history backup, never accidental revocation of one of the two operational leaves. The initial bootstrap command intentionally cannot issue extra leaves after initialization. Prepare that bounded issuance/retirement tooling before requesting its operational approval |
| Existing-session containment | Preserve [the incident procedure](ISSUER.md#compromise-containment-and-live-acceptance). A previously allowed tiny backup PUT is the live denial witness after an approved temporary deny. Bedrock uses installed-policy evaluation and the one-hour expiry boundary unless paid-call-risk approval is separately given; `GetCallerIdentity` does not prove denial |
| HM4 | Reviewed helper/image and real SDK exchange/refresh; home app/Secret mounts; GitOps annotation ownership and sync survival. Names above are a contract, not installed workloads |
| HM5 | Renewal/CRL refresh/backup schedules; independent expiry/heartbeat/availability alerts with tested delivery. Mac renewal currently uses LAN SSH; any away-renewal transport change needs its own verification. Bootstrap's use of the verified SSH alias does not modify renewal |

Initial bootstrap acceptance is verified. [Subsequent CRL/authentication acceptance](HM2-ACCEPTANCE.md)
records the live proofs and complete revoked history; actual workload integration and
installed recurring schedules stay in HM4/HM5. HM3 production export/restore is unstarted.
