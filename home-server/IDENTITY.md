# HM2 home-server AWS identity — September 13, 2026

**IAM Roles Anywhere is provisioned with authentication disabled, September 14.**
[Applied verification](IDENTITY-APPLIED.md) records eight creates and a no-change follow-up
plan. Initial backup/Bedrock leaves and Secrets were separately approved and
[bootstrapped September 15](IDENTITY-BOOTSTRAP.md), with independent populated-ledger
recovery from S3 using a home-held public checkpoint. Original CA/age custody is preserved.
CRL, authentication enablement and scheduling remain separately gated. No AWS workload
session or application deployment has occurred. Future slices retain commit/cloud review
gates. [Original enrollment record](ISSUER-ENROLLMENT.md).

**Next slice prepared locally, September 13:** [issuer enrollment/recovery/CRL tooling](ISSUER.md)
adds native no-overwrite enrollment, independent complete-ledger recovery and signed CRLs
with public AWS readback validation. Source merged in PR #21; separately approved enrollment
and S3 recovery verification are complete. No CRL/cloud identity/scheduler action has occurred.
It introduced bundle/ledger schema 2 before first use.

Steve approved the certificate operating design in this session: a private CA signing key
in the Mac's local Keychain, an age-encrypted issuer recovery bundle in S3 and on the Mac,
and 90-day workload certificates. Steve then required automatic renewal 30 days before
expiry; a Mac launchd job now replaces the manual-renewal proposal. This approval
selected the design; the later bounded operational approval enrolled only the issuer and
proved its initial recovery. E21 still precedes P39; AWS remains production.

## Identity and permissions

A **certificate authority (CA)** signs certificates binding public keys to identities.
Our CA runs as an operator procedure on the Mac. Its public certificate becomes the AWS
**trust anchor**. Example: the CA signs the backup workload's public key with subject
`CN=driftplain-home-server-backup`; the matching private key stays with that workload.
A public web CA is unnecessary. This is independent of public HTTPS certificates.

| Workload | Certificate subject CN / IAM role suffix | Allowed AWS operations |
|---|---|---|
| Backup job | `driftplain-home-server-backup` / `home-server-backup` | `s3:PutObject` under `postgres/hourly/*`, `postgres/daily/*`, `recovery/*` in the dedicated backup bucket |
| Backend | `driftplain-home-server-bedrock` / `home-server-bedrock` | `bedrock:InvokeModel` on the existing Nova Lite / Nova 2 Lite inference profiles and their exact models, with foundation-model calls conditioned on those profiles |

Each role requires the exact trust-anchor ARN, account, issuer CN
`driftplain-home-server-issuer-v1`, and its own leaf CN. Each Roles Anywhere profile names
only its corresponding role and repeats its permission policy as a session ceiling.
Changing the requested role/profile cannot turn a backup certificate into a Bedrock identity.
The default subject/issuer attribute mappings must be checked after provisioning; missing
tags fail the exact trust conditions. Profiles alone are not the isolation boundary.
[AWS trust rules](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/trust-model.html)
and [attribute mapping](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/attribute-mapping.html).

Neither role grants Secrets Manager, IAM, ECR, Terraform-state access, backup reads/deletes,
bucket administration or role chaining. Bedrock gets no S3 ingestion permission in this
slice; the existing in-memory blob/reference limitation remains an HM4 decision. Existing
operator-only paid-feature access and database-backed hourly token caps remain required;
IAM identity is not a replacement for those controls. The model/profile scope mirrors
[platform/irsa.tf](../platform/irsa.tf) and [platform/dev.tfvars](../platform/dev.tfvars).

**Write-only backup choice in this review draft:** upload the same locally encrypted archive
again under the daily prefix, rather than copying it inside S3. The scheduled job uses the
low-level single-request `PutObject` with a supplied SHA-256 checksum and records the returned
version/checksum. It must reject archives above [S3's single-PUT limit (5 GB)](https://docs.aws.amazon.com/AmazonS3/latest/userguide/upload-objects.html) and alert; do not
use transfer-manager `upload_file` with implicit multipart uploads. No HEAD/Get/List/Copy,
AbortMultipartUpload or Delete grant is added speculatively. HM3/HM5 must verify this actual
upload contract and measured size before scheduling. This adds one archive upload per day.

Object names include time plus a unique backup ID. Versioning protects older versions from
ordinary overwrites; PutObject alone does not enforce create-only or immutability. A stolen
uploader identity can write junk/extra versions and incur storage charges. Independent
operator download/decryption/restore is the verification authority. A successful PUT or
checksum alone is not a database restore. The recovery private key never goes to either job.

## Terraform ownership and deployment gates

[identity/](identity/) is a new persistent root: state key
`home-server/identity/terraform.tfstate` in the existing protected state bucket. It references
the exact existing backup bucket name as a validated input, without importing the bucket or
reading all bootstrap state. It owns only identity resources and an additive teardown deny.
`stack=home-server-identity` tags distinguish them from retired compute.

The applied [dev.tfvars](identity/dev.tfvars) sets creation **true** and session
enablement **false**, using the verified enrolled public CA. The fresh September 14
[plan review](IDENTITY-PLAN.md) confirmed **8 creates, 0 updates, 0 deletes**. After separate
approval, an equivalent refreshed saved plan was applied and [verified](IDENTITY-APPLIED.md).
The complete follow-up plan proposes no changes; authentication remains disabled.
The eight creates are one trust anchor, two
roles, two inline workload policies, two profiles and one extra policy on the existing
teardown role. That last resource changes an existing principal's effective permissions.
It denies Roles Anywhere administration, access to both home-server roles and access to the
new identity state prefix, without rewriting bootstrap's existing guardrails. Establish it
before the trust anchor. `prevent_destroy` protects the anchor, profiles, roles and guard.
It is not protection from an AWS administrator changing policies/configuration.

The separately approved publication of earlier HM2 work includes the full applied
budget/S3/ECR and recovery-custody configuration. Use current main with a fresh reviewed
plan; historical partial checkouts must not be applied. The approved budget safeguard
stays `DRY_RUN=1`; source publication does not apply cloud changes.
After deployment, disabling authentication means `home_server_sessions_enabled=false`,
not changing the resource-creation flag (which would propose protected destruction).

1. Before disabled provisioning: verify issuer custody and independent empty-ledger
   recovery, then review the enrolled public CA fingerprint/constraints. This is complete;
   do not re-enroll or issue leaves to prepare this plan. No private material goes into HCL,
   tfvars or state. The PEM variable validates framing only; AWS and a local X.509 check must
   validate actual signatures, validity, issuer and key usage. Test placeholders are not CAs.
2. Obtain a reviewed live plan from this root, with explicit `-var-file=dev.tfvars`, normal
   state locking, account `957261948820`, region `ap-south-1`; approve before applying.
   Initial resources can be provisioned with authentication disabled.
3. Disabled provisioning, actual role/profile policies, default attribute mappings and
   teardown denials are now verified in the applied record. Initial leaf bootstrap is a later
   approved operation; record the complete history and repeat independent recovery.
   Prepare and test CRL import/update plus emergency session denial before enabling sessions.
4. Separately approve enablement and prove a valid exchange, cross-role rejection,
   wrong-anchor rejection and revoked-leaf rejection. STS identity checks do not invoke an
   LLM. Prove revocation using a disposable leaf, never a production credential by accident.
5. HM4/HM5 deploy the reviewed helper image and separate GitOps profile; verify non-root
   mounts, refresh across expiry, renewal/restart behavior and alerts before public use.

## Certificate custody, renewal and recovery

An **issuer recovery bundle** backs up the CA's signing key, public certificate, issued serial
ledger, revocation ledger and signing configuration. Encrypt it with `age` to the existing
[recovery recipient](recovery-key-v1.recipient). Only the resulting ciphertext is stored in
S3 (`recovery/issuer-v1/<unique-id>.age`) and on the Mac. Record the S3 version, ciphertext
hash and recovery-key version. These are separate immutable-named copies, not a mutable latest.
The existing off-host recovery key unlocks the bundle. Never include the recovery private key
inside it. The CA's public certificate/fingerprint may be committed after review.

The CA signing key belongs in a **new** local login Keychain item
`modelmatch/home-server/issuer-v1`, account `steve`; do not overwrite or repurpose
`modelmatch/home-server/recovery-key-v1`. Enrollment must use native Keychain access with memory/pipe
handling like the custody helper; the renewal reader already uses those native APIs, without key/password arguments or plaintext temp files.
Keychain is off the Ubuntu host, not an air-gapped system. Mac compromise can expose signing
authority. S3 and the AWS-held recovery key share an account; the Mac copy is the independent
location. No additional Secrets Manager secret or AWS Private CA is selected.

Enrolled CA parameters (initial leaves verified September 15): dedicated self-signed RSA-3072
CA, SHA-256, two-year validity, X.509v3 `CA:true,pathlen:0`, `keyCertSign,cRLSign`; directly
issued RSA-3072 leaves with `CA:false`, `digitalSignature`, one exact CN and 90-day validity.
Verify leaves never outlive the CA. Keep a ledger of serial, subject, public-key fingerprint,
issue/expiry time and replacement/revocation state. Serialize issuance to prevent stale
ledger recovery or duplicate serials. Preserve the full ledger in every updated encrypted
bundle; test recovery before the first trust anchor is enabled.

**Leaf-key delivery:** generate a new key for each identity on the home host in a root-owned
private staging directory, export only its CSR (certificate signing request), verify its
subject and key, then have the Mac CA sign the approved identity. Never blindly copy CSR
extensions. Return only the signed certificate. Delivery uses strict-key SSH and `sudo -n`.
The operator installs each pair into its own Kubernetes Secret via stdin, without displaying
YAML/base64 or putting keys in argv/Git. Remove staging keys once delivery is verified.
This bootstrap delivery needs no resolution of the separate ongoing app-secret-store choice.

**Isolation:** dedicated backup namespace/service account and a different backend Secret;
each pod mounts only its own key, read-only, with mode `0440` and an owning group matching
the non-root workload. Service accounts get no Secret-read or pod-create permissions;
disable API token automount when unnecessary. Mount the entire Secret directory, not
`subPath`, so rotation can propagate. Encrypted K3s etcd is still required. Do not put leaf
keys in general recovery bundles/etcd snapshots without encryption. Host root/cluster-admin
can access both identities on this single node; this is workload isolation, not host-compromise
isolation. Reissue leaves after loss rather than restoring their private keys.

**Automatic renewal:** [home-server-renew.py](home-server-renew.py) runs on the Mac;
[home-server-leaf.py](home-server-leaf.py) handles the home side over strict-key SSH and
`sudo -n`. The disabled [launchd job](dev.driftplain.home-server-renewal.plist) runs at login,
at 09:00 local time and hourly for retries; successful checks are cached for 20 hours.
At 30 days remaining it generates a fresh host-held key/CSR, validates/signs the CSR on
the Mac, journals the public certificate before delivery, and encrypts/verifies an S3 issuer
bundle before changing the Secret. Failed upload leaves the existing Secret intact. Retries
reuse the pending certificate; a stale journal cannot replace an unrelated newer leaf.
A failed final bundle upload is explicitly marked for retry.

The home helper checks the signed certificate against its enrolled public CA and pending
key, then replaces the existing Secret with resourceVersion concurrency protection. It
changes one public certificate-hash annotation in the backend pod template and waits for
rollout; backup Jobs consume the new Secret on their next start. Retries after lost responses
reconcile the same hash instead of starting repeated rollouts. Private staging files are
removed only after successful delivery. The earlier proposal to manually revoke every old
leaf is replaced by normal expiry of the old leaf after its remaining 30 days; the old
private key is discarded. Suspected compromise still requires immediate revocation.

**GitOps ownership prerequisite:** the home profile must explicitly delegate only
`spec.template.metadata.annotations["driftplain.dev/home-server-certificate-sha256"]`
to the renewer (ArgoCD ignoreDifferences plus RespectIgnoreDifferences), reference the
operator-managed identity Secrets, and verify that a sync does not undo rotation. All other
Deployment fields stay owned by GitOps. Do not install the renewer before this is tested.
Workload rollout readiness does not prove a new AWS credential exchange; HM4 must establish
a token-free identity smoke/check and refresh-failure alert as deployment acceptance.

The Mac must be logged in/awake, Keychain access authorized for the stable renewal Python
executable, and home SSH/S3 reachable. No password is stored or prompted by the job. It
records a sanitized status file, exits nonzero and sends a macOS notification on failure;
hourly retries remove the need to remember a calendar date. Mac sleep/offline/logout or a
broken Python/job installation can still prevent execution. An independent expiry/renewal
heartbeat monitor is a mandatory HM5 backstop. [Apple launchd behavior](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).
AWS session refresh does **not** renew certificates or access the Mac signing key.

**Enrollment/install gate:** no task is registered now. Preserve the enrolled issuer,
stable Mac runtime and verified empty ledger. Initial leaves and populated-ledger recovery
remain separately approved steps before enabling authentication. The prepared Mac
scripts (including `recovery-key.py`, imported only for native API/pipe utilities, and the
public `recovery-key-v1.recipient` used to reject an unexpected encryption recipient) and a
stable Python 3.12 environment with `boto3==1.43.24`, `botocore==1.43.24`,
`cryptography==50.0.1` are installed; the operator config already pins the public CA
fingerprint. Preserve these installed files independently of working-tree changes; see
[the operational record](ISSUER-ENROLLMENT.md). Install the root-owned home script/public
issuer certificate only under its later scope; verify exact Secret,
namespace and Deployment names against the future home GitOps render. The script requires
existing Secrets and never bootstraps missing identity state itself. Only then approve
`enabled=true`, the launchd enable/install and its scoped recurring S3/Secret/annotation
writes. Test noninteractive Keychain access and an actual scheduled retry/rotation first.

**Alerts/maintenance:** HM5 must monitor public certificate expiry daily, warn at 30 days,
escalate at 14 and 7, and alert on expiry/refresh failure and a stale renewal heartbeat.
The Mac job warns when CA validity reaches 180 days, while continuing eligible leaf renewal; CA replacement
planning starts 180 days before expiry with escalation at 90/30. Test external notification
delivery; a home-only check cannot alert on total host loss. Time synchronization matters
for both certificate validity and AWS signatures. No timer/alert has been installed here.

**Revocation:** a CRL (certificate revocation list) is the CA-signed list of revoked serials.
Roles Anywhere checks CRLs imported into AWS; it does not fetch a URL in the certificate or
call OCSP. [The prepared issuer workflow](ISSUER.md) retains the ledger, generates signed
CRLs and validates public AWS readbacks; the operator procedure imports against the exact
trust-anchor ARN and updates the recorded CRL ID. Live import/enabled-state and negative
authentication proof remain gated. Keep CRLs current (prepared monthly refresh with a
35-day nextUpdate, immediate refresh after revocation); do not rely on expiry behavior as
an emergency control. Leaf renewal does not refresh CRLs; independent monitoring is required.

**Lost/stolen host:** disable affected profiles immediately under applicable incident
approval, add a deny-all policy on the affected workload role to stop already-issued sessions
while the incident is contained, revoke both leaf serials and update/verify the CRL. Disabling
a profile/anchor or revoking a certificate alone does not invalidate existing STS credentials;
they can remain usable for up to the configured hour. IAM policy changes also take propagation
time. Restore data with the operator identity, issue fresh keys for a trusted replacement,
prove old-leaf rejection and new-leaf operation, then review removal of the temporary deny
and restore authentication. Reconcile emergency IAM changes back into code before a plan
can accidentally undo them. [AWS session revocation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_revoke-sessions.html).

**Lost Mac:** recover the age key through the already-verified AWS custody path, download
the exact issuer bundle version with the operator identity, decrypt on a trusted replacement,
restore the local Keychain item/ledger, and verify the public CA fingerprint before issuing.
**CA compromise:** disable the entire trust anchor and deny sessions for both roles; create
a new issuer/trust anchor and replace both leaves. Recovering the old key does not make it
trustworthy again. Keep historical encrypted bundles according to recovery needs; no automated
CA/backup-key deletion is introduced.

## Credential refresh and image contract

[home-server-identity-config.py](home-server-identity-config.py) renders a separate public
AWS config for one identity. Its `run` command selects that profile, empties the shared
credentials-file path, disables instance metadata and legacy boto config, and rejects
static/IRSA/container credentials. Config/helper/scripts must be image-owned or read-only
ConfigMaps, never workload-writable. No Mac AWS directory is mounted.

[home-server-credentials.py](home-server-credentials.py) verifies the helper SHA-256 before
execution, checks leaf-file availability/private-key mode, imposes a 30-second subprocess
deadline, validates temporary credential fields/expiry and redacts all helper error output.
Successful stdout contains secrets and is **only** consumed by the SDK pipe. Never run it in
a captured terminal, log the JSON or export its values as static environment variables.
It does not cache credentials on disk or start a shared credential HTTP server.

The existing [Bedrock client](../../driftplain-backend/app/llm/bedrock_client.py) uses normal
boto3 discovery and retains its client. The SDK refreshes the process credentials on demand
before expiry. During an advisory refresh failure, the SDK may continue with a still-valid
session; mandatory refresh failure stops signing. There is no fallback to an administrator
or fake answer. One-hour sessions are set on both the profile and helper; the IAM role maximum
also remains one hour. [AWS process credentials](https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html)
and [session duration](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/authentication-create-session.html).

Pinned helper **1.8.5**, Linux x86-64, from
[AWS's download table](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html):
`https://rolesanywhere.amazonaws.com/releases/1.8.5/X86_64/Linux/Amzn2023/aws_signing_helper`,
SHA-256 `beec9ed1c492d93db809890f16713e3556353294b823c2184ad4e891f1b2b54d`.
The renderer pins this hash. HM4 must fetch/check it during the reviewed image build, run
`version`/`credential-process --help` and verify Linux compatibility in the actual image;
never download latest at pod startup. Install at `/usr/local/bin/aws_signing_helper`, scripts
at `/opt/home-server/`, Python at `/usr/local/bin/python3`. The existing Python backend image
supports that interpreter path; helper/runtime image build and GitOps mounts remain unshipped.

Public config preparation after provisioning uses `terraform output -json home_server_identity`
as the renderer input (not the envelope from `terraform output -json`). The profile config and
its matching Secret are mounted at `/run/home-server-identity/aws-config`, `tls.crt`, `tls.key`.
The eventual workload entrypoint calls `home-server-identity-config.py run --identity bedrock
--config /run/home-server-identity/aws-config -- gunicorn ...`; backup uses `--identity backup`.
The Terraform root, operator SDK tests and production image have separate execution contexts.

## Costs and evidence

Roles Anywhere has [no additional service charge](https://aws.amazon.com/about-aws/whats-new/2023/12/iam-roles-anywhere-additional-aws-regions/).
The operator-managed CA adds no AWS Private CA or extra Secrets Manager fee. The existing
recovery-key secret remains approximately **$0.40/month + $0.05/10,000 API calls**;
[AWS pricing](https://aws.amazon.com/secrets-manager/pricing/). Issuer bundles add small S3
storage/PUT costs at the [existing Mumbai rates](S3-BACKUPS.md); the second daily archive PUT
has the same request-class price as the previously proposed COPY, but uses more home upload
bandwidth. Bedrock tokens, retained services, recovery downloads and optional monitoring/audit
storage still cost normally. No promotional credits, new paid subscription or LLM calls assumed.
There is no complete new monthly bill estimate until HM2's remaining service choices are settled.

Local verification (no AWS calls):

```bash
terraform -chdir=home-server/identity init -backend=false -lockfile=readonly
terraform -chdir=home-server/identity validate
terraform -chdir=home-server/identity test -var-file=dev.tfvars
uv run --no-project --python 3.12 --with boto3==1.43.24 --with botocore==1.43.24 \
  python home-server/test-home-server-identity.py
uv run --no-project --python 3.12 --with boto3==1.43.24 --with botocore==1.43.24 \
  --with cryptography==50.0.1 python home-server/test-home-server-renewal.py
```

Seven mocked Terraform contracts, eight process/SDK tests and eleven renewal tests pass. The SDK versions
match the backend lockfile; dependencies were isolated from its existing environment. Tests
exercise real request signing on the same unchanged backend client before/after simulated
refresh, deny mandatory refresh after helper failure, and cover error redaction, malformed/
expired credentials, helper tampering, missing/exposed leaf keys, conflicting credential
sources and mismatched configuration. Socket connections are forbidden in the SDK test. Renewal tests use disposable in-memory
CA material and real local OpenSSL, with fake Keychain/SSH/S3/Kubernetes boundaries; they
cover due/not-due, interrupted backup/delivery, stale journals, wrong CSR identities,
CA privilege rejection, issuer validity, locked Keychain, key-pair rejection and rollout retry.
An additional fresh-fixture age round trip verifies issuer-bundle encryption, version-pinned
S3 readback through a fake client, and absence of plaintext CA keys in output files.

Limits: mocked policies are not live IAM enforcement proof; fake credentials are not a signed
Roles Anywhere exchange. Operational issuer custody and empty-ledger recovery now pass in
the separate enrollment record. Certificate renewal, imported CRL, container installation,
scheduled backup and alerts remain unverified live. Those gates remain explicit above. See
[sanitized local evidence](hm2-identity-local-evidence.json).
