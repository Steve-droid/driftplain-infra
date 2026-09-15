# HM2 operating-design and identity acceptance — September 15, 2026

**HM2 complete and accepted September 15 under Steve’s continuation authorization.**
[Sanitized evidence](hm2-acceptance-evidence.json) records all live proofs, selected decisions
and verified cleanup.
HM3 has not started. AWS still serves production; no public route, data export/restore,
application Deployment or schedule is part of this acceptance.

Steve approved the bootstrap evidence and then directed continued HM2 work, pausing only
for critical architectural decisions. That supersedes repeated routine source/publication
and operational pauses for this continuation. Paid LLM calls, public cutover and eventual
production teardown remain outside this work. Bootstrap evidence is published through
[infra #26](https://github.com/Steve-droid/driftplain-infra/pull/26) and
[portfolio #28](https://github.com/Steve-droid/driftplain-portfolio/pull/28).

## Acceptance against the active HM2 slice

| Requirement | Evidence / result |
|---|---|
| Reboot, service and router recovery | Previously verified in [RESULTS.md](RESULTS.md) and [RECOVERY.md](RECOVERY.md); unchanged drills were not repeated |
| Host/storage choice | Ubuntu Desktop, lid/AC settings, Samsung storage and unused Kingston preserved; one K3s node/one future CNPG instance, explicit Retain storage contract |
| Backups and recovery custody | S3 destination and age custody selected/applied; issuer backup now independently recovers issued and revoked history from a home-held checkpoint. Production DB backup/restore remains HM3 |
| AWS identity | Initial Secrets verified; actual matching-role, cross-role/profile, wrong-anchor and revoked-leaf checks pass. Temporary authentication enablement is reversed; containment cleanup is verified below |
| Images, app secrets, source blobs | Steve selected public GHCR for four images, Sealed Secrets plus encrypted controller-key recovery, and a durable S3 ingestion adapter. Existing Jenkins setup already rejects secret inputs. HM4 implements these choices |
| Public route | Steve selected Cloudflare Tunnel with full DNS for both registered domains, preserving Porkbun/Google identity. Home resolves and reaches both documented tunnel endpoints over TCP 7844. No public inbound-IP assumption; ISP CGNAT remains unknown. HM5 tests actual tunnel/reconnect/public staging |
| Rollback and old hosts | Steve selected at least 48 healthy hours before retirement review; failures extend the window. After home writes, rollback requires a write freeze and verified newest-data transfer. Modicum stays supported; AWS-IP sslip.io hosts retire only in HM8 after integrations migrate |
| Recurring costs/ownership | Steve accepted $10/month retained services/domains plus ₪36/month power as conservative planning assumptions, excluding AWS overlap and paid LLM calls. Exact wall readings/account renewals remain pre-cutover checks. [Worksheet](HOME-SERVER-COSTS.md) includes measured ECR/S3 quantities and sources |
| Source-data safeguard | DRY_RUN=1 retained. No automated teardown, identity/key replacement or production mutation |

## CRL correction and complete history

AWS rejected the initially generated empty CRL with `ValidationException`: it requires
at least one revoked certificate. The old runbook's empty-import assumption was incorrect.
The failed import created no CRL; its signed ledger/archive remains preserved as number 1.
The operational CA was never rebuilt or re-enrolled.

Two disposable leaves were issued with the existing backup CN, using private keys generated
only in a root-owned home acceptance directory. The [test-leaf issuer](home-server-acceptance-issuer.py)
journals their purpose/serials and verifies encrypted S3 backup before publishing public
certificates. It never replaces a workload Secret. Five focused tests pass, covering
retry/CSR conflicts/history preservation, memory-only helper handling and rejection of
transport errors as false AWS-denial evidence. Existing suites were not repeated.

| Certificate | Serial | Treatment |
|---|---|---|
| Initial backup | `188550183090849239859899631588670510961450592200` | Unchanged, unrevoked |
| Initial Bedrock | `308719772415312223547387245572677494936844969648` | Unchanged, unrevoked |
| CRL initialization test | `517933271547206468676772142300805663327053611682` | Revoked before any authentication; allows nonempty CRL import |
| Revocation proof test | `146669769643444471910834356540355106435297546538` | Valid exchange observed, then revoked; fresh exchange rejected |

CRL **2** imported successfully with the first revoked test leaf. CRL **3** updates that
same AWS object with both test revocations. Imported CRL ID:
`346c7fd3-e782-4500-a72c-53b8741ff543`, enabled, attached to operational anchor
`913f6b1b-d09a-41be-8715-e41e04ecda90`. Exact readback, CA/signature, full serial set,
number and validity pass. The imported CRL remains enabled when authentication is disabled.
Monthly CRL refresh and independent deadline alerts remain HM5; preserve revoked history.

The latest independent recovery reconstructs the exact full ledger, four issued leaves,
two revocations, no pending delivery and CRL number 3. Canonical digest:
`ed94ca9e8b4fd846aadcc4eb36d61bd1f32cb74cd23e5446a387e9b537f2a2b4`.
It used an independently retrieved public checkpoint at
`/home/steve/.local/share/driftplain/home-server-issuer-checkpoints/20260915T133245Z-ed94ca9e8b4fd846aadcc4eb36d61bd1f32cb74cd23e5446a387e9b537f2a2b4/`
and the original AWS recovery-key version, with original issuer custody/files unavailable
to the isolated recovery command. This extends the initial two-leaf proof; it is not a
production database restore or replacement-Mac native restoration.

## Live authentication and containment

The complete normally locked enablement plan used explicit `-var-file=dev.tfvars` plus
the temporary session flag override. Reviewed saved-plan SHA-256:
`2ff34c072f1891b6646dc236b14281669a994ad065ca008c137f09042d89a7d6`.
Only three enabled flags changed: the operational anchor and its two profiles. No role,
permission, source, resource-creation flag or production Terraform stack changed.

The temporary [host acceptance harness](home-server-acceptance-host.py) used AWS helper
1.8.5 with the published SHA-256 and pinned pure-Python boto3/botocore 1.43.24 dependencies
on home Python 3.14.4. It reads workload keys from Kubernetes through captured pipes and
passes them to the helper via anonymous memory file descriptors; temporary credentials
stay in process memory. No credential-file cache, HTTP credential server or Mac AWS
credentials are copied to home. The harness only permits STS and tiny encrypted S3 writes;
it cannot create a Bedrock client. This is an operator test, not HM4 image/SDK integration.

| Observed UTC | Proof |
|---|---|
| 13:27:49–50 | Initial backup, Bedrock and revocation-test leaves obtain their exact matching assumed roles |
| 13:27:51–53 | Backup certificate rejected for Bedrock role/profile, wrong profile with backup role, and alternate anchor |
| 13:27:54 | Original backup session writes a tiny age-encrypted witness under its permitted recovery prefix |
| 13:28:35–36 | Exact temporary deny-all policies applied/read back on both workload roles |
| 13:29:15 | That same original backup session loses its previously allowed PUT permission (`AccessDenied`) |
| 13:30:26 | Revoked test leaf's fresh CreateSession is rejected after CRL update |
| 13:30:28 | Unrevoked original backup certificate still obtains its intended role, proving the revoked rejection is not universal failure |
| 13:32:45–46 | After disabling authentication, fresh exchanges using both workload leaves are rejected |

The wrong-anchor test used a temporary enabled anchor holding the **same public CA**, so
it exercises the role's exact anchor-ARN restriction. Temporary anchor ID
`46c5a72d-4712-45ed-92f6-1cd3e572c03c` was deleted during verified cleanup. No new operational CA,
profile or IAM role was introduced.

The complete disabling plan used committed `dev.tfvars` without an override and again
changed only the same three enabled flags. Saved-plan SHA-256:
`43e6a9f4c741ac5983c8f82be65c7ed7f542000d0dfea732f0b9497636cd6af4`.
Temporary role denies remain until every observed test session expires. Acceptance requested
15-minute sessions within the existing one-hour maximum; actual returned expiries were
checked. Latest successful exchange expires at **13:45:27 UTC**. Cleanup retains a safety
margin and verifies old-session expiry before removing only the two test denies.

Bedrock containment evidence is the exact installed deny-all policy, installed-policy
simulation and session-expiry boundary. No InvokeModel call was made, so this is not live
Bedrock action-denial proof. STS GetCallerIdentity was used only for role identity, never
as a permission-denial witness. No paid call was authorized or attempted.


## Final state and cleanup

At **13:47:01 UTC**, after the last successful test session's expiry plus approximately
95 seconds, only the two temporary deny policies were removed and the alternate test
anchor was disabled/deleted. At **13:47:27 UTC**, the original backup session still returned
`ExpiredToken` after policy removal. The credential-holding process exited successfully.
At **13:48:31 UTC**, its isolated host runtime/dependencies and both revoked disposable
private keys were removed; original workload key pairs/certificates and their Secrets
were reverified unchanged. No app or backup workloads exist in either namespace.

The final normally locked Terraform plan at **13:47:42 UTC**, explicit `-var-file=dev.tfvars`,
exits **0** with all eight resources and outputs no-op. JSON has two readback differences:
each role's observed `inline_policy` collection loses the intentionally removed temporary
deny. These are not planned resource changes; no corrective/refresh-only apply was made.
Final AWS readback at **13:49:58 UTC** verifies exact original trust/workload/session
policies, disabled anchor/profiles, original CA and recovery-key version, enabled CRL 3,
disabled configs, absent scheduler and `DRY_RUN=1`.

Final issuer archive: **14,487 bytes**, key
`recovery/issuer-v1/691054fb88d53b71856b295deccaf2a2d12a25206610e3417a98dc31ba75f1de.age`,
version `OgYzVHUZ6sC4QVvix7AZkGS7wjvaKxlw`. CRL nextUpdate is **October 20, 2026,
13:28:36 UTC**; refresh it before that date and before any later enablement if stale.
No timer was installed. Test probe IDs are reserved in the retained ledger: do not delete
history or reissue them to rerun already accepted checks.

## Exact later-slice dependencies

- **HM3:** fresh consistent encrypted production export, original credential recovery,
  independent S3 download and CNPG restore with complete data/identity/sequence comparison.
- **HM4:** reviewed current-release images and anonymous pulls; S3 adapter and its scoped
  IAM addition; home GitOps/Sealed Secrets ownership; helper image and non-root mounts;
  actual SDK refresh and rotation/sync acceptance. Re-enable authentication only as part
  of that prepared scope. Do not call the operator harness an application deployment.
- **HM5:** installed hourly backup/retention, leaf renewal and CRL refresh schedules;
  independent availability/expiry/heartbeat alerts; actual Cloudflare tunnel, verified
  origin TLS, streaming/restart tests and public staging; wall-power/domain-account checks.
  Renew current leaves from November 14, 2026. Remote-access hotspot validation remains
  a before-departure operator check; a successful tailnet connection alone is not proof.
- **HM7/HM8:** approved public cutover, one authoritative writer, measured overlap cost,
  the selected rollback window and separately approved retirement. Keep AWS running until then.

Raw plans, public receipts and sanitized live results are retained under
`/Users/steve/.local/share/driftplain/home-server-identity/acceptance-20260915/`.
Current source branches are `feature/e21-hm2-identity-acceptance` in umbrella and infra.
Earlier handoff/progress edits and all historical issuer archives remain preserved.
