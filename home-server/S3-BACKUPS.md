# HM2 S3 backup destination and recovery design

September 13, 2026. Steve selected **S3** to reuse the existing AWS platform and approved
**hourly backups**, hourly retention of **one day**, daily retention of **30 days**, and a
**four-hour restore target** once working hardware/internet are available. Backup success
must support a one-hour recovery-point target. These targets still require implementation
and measurement; no production backup or home restore exists yet.

## Bucket foundation — approved and applied September 13, 2026

Applied reviewed bootstrap plan: **6 creates, 1 in-place update, 0 deletes**.
The six creates are one bucket and five configuration resources, not six buckets.

- Dedicated `modelmatch-home-server-backups-957261948820` in `ap-south-1`, persistent bootstrap state.
  `prevent_destroy=true`, `force_destroy=false`; separate from state and ingestion buckets.
- All public access blocked, ACLs disabled through BucketOwnerEnforced, versioning enabled,
  SSE-S3 AES256 encryption at rest, and a bucket policy denying non-HTTPS access.
- Bucket policy explicitly denies every S3 action by the platform teardown role. Its existing
  identity guardrail also gains the same bucket/object deny; all previous guardrails remain.
- No lifecycle expiry yet. Enable the approved retention only after a successful independent
  restore, through its separately reviewed change. No uploader role, static keys, certificate
  authority, paid KMS key, backup schedule, production export or public-route change is included.

[Plan evidence](hm2-s3-plan.json). Terraform validate and four mocked contracts pass; negative
cases reject reusing state/ingestion buckets and invalid names. Parsed full-plan checks verify
only these seven resource actions and both bucket-policy denies. Nine read-only IAM simulations
confirm backup object/bucket/configuration actions are denied even alongside an S3 allow, while
required state-object Get/Put remain allowed. After apply, read-only AWS checks verified the bucket's actual region, versioning, ownership,
encryption, public-access blocks, policy and empty contents. Nine simulations against the live
teardown role confirm its backup denials and required state access. This does not substitute
for a real backup upload/download/decryption and database restore in HM3.

[Applied evidence](hm2-s3-applied.json): production API ready/db ok, two healthy CNPG instances,
Lambda remains DRY_RUN=1, and the full follow-up Terraform plan reports no changes.

The private reviewed plan formerly at `/tmp/driftplain-hm2-home-server-s3.1G2NLw/reviewed.tfplan`
was removed in the approved September 13 cleanup; its hash remains in the JSON summary.
Saved-plan/source hashes were verified before the original approved apply with normal locking.
Source publication was separately approved September 13; future changes need a fresh reviewed plan.

## Backup contents and retention

Use unique immutable object names, not a repeatedly overwritten `latest` object:

| Prefix | Contents | Approved retention direction |
|---|---|---|
| `postgres/hourly/` | Consistent encrypted PostgreSQL custom-format export and integrity manifest | One day |
| `postgres/daily/` | Same encrypted hourly export uploaded again under a daily name (write-only identity draft) | 30 days |
| `recovery/` | Encrypted credential/configuration recovery bundle, tied to the database/release manifest | Preserve each bundle needed by any retained DB copy |

The recovery bundle includes required role/schema restoration information, exact app credential
values, release references, and host/K3s recovery material. K3s etcd snapshots need the matching
server token and do not include PVC data. The first migration copy is also held encrypted on the
Mac; after provisioning, pull an independent copy from S3 and restore that copy during HM3.

S3 day-based expiry rounds up to midnight UTC and physical removal is asynchronous; one-day
hourly retention can therefore retain more than 24 hourly objects. Versioned buckets also
require explicit noncurrent-version and delete-marker cleanup. Do not promise exact copy
counts or immediate removal at 30 days in product privacy text. Check actual versions/bytes
and lifecycle behavior in HM5. A current-object expiration alone does not remove old versions.
[Official lifecycle timing](https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html).

## Encryption and access

Client-encrypt each export before it leaves the producing host, in addition to S3 encryption.
Tool: age public-key encryption. Home holds only the encryption recipient/public key.
Steve selected AWS Secrets Manager plus a local-only macOS Keychain copy on September 13.
The versioned secret is separate from runtime app secrets; its private value stays outside
Terraform state. [Custody and independent decryption evidence](RECOVERY-KEY.md) documents
the two stores, operator-only access, teardown protections and recovery procedure.
Do not store the private recovery key on home or in the backup bucket.
The manifest must detect incomplete/corrupt downloads; decryption plus an actual DB restore
is the acceptance test. Preserve old recovery keys while retained backups still need them.

Initial HM3 operator export/upload may use Steve's existing local AWS session on the Mac.
For automatic home uploads, prepare a separate certificate-authenticated IAM Roles Anywhere
role with temporary credentials: write only to required backup prefixes, with no object-delete,
version-delete or bucket-policy/lifecycle access. Keep it separate from the Bedrock role.
IAM Roles Anywhere is selected. The [bounded local identity implementation](IDENTITY.md)
contains exact certificate-bound trusts, separate permission policies and SDK refresh tests.
Its write-only draft repeats the local encrypted archive upload for the daily copy; operator
reads perform independent verification. CA custody is selected and automatic leaf renewal
is required; the CA and its encrypted Mac/S3 recovery bundle are now enrolled/verified,
with a public checkpoint on home ([record](ISSUER-ENROLLMENT.md)). No workload identity,
leaf certificate or scheduler is deployed yet. Never copy Mac admin
credentials or static IAM keys onto home. No AWS Private CA charges are introduced.

## Recovery and failure behavior

After host/disk loss: restore access to a working host, retrieve/decrypt the independent backup,
create the approved CNPG/storage profile, restore schema/data/roles/sequences and original app
credentials, compare integrity/identities, then validate the private application before exposing
writes. Do not seed restored data. Measure against the four-hour target; replacement hardware
procurement and an unavailable operator are not measured restore performance.

Alert on every failed scheduled backup and when the last successful off-site copy is older
than two hours. Detect gaps using the upload-success manifest, not merely a started CronJob.
Hourly cadence alone does not guarantee a one-hour recovery point: include export/upload
latency when measuring it. Restore using S3-only assets and documented key custody before cutover.

S3 is off-site but remains in the same AWS account. These controls protect against routine
platform teardown and accidental Terraform deletion while the guard remains in configuration;
they do not make backups immutable or immune to an AWS account administrator/compromise.
The extra encrypted Mac copy provides another recovery location. No Object Lock/WORM guarantee
is claimed. Broad account administrators can change policies; keep their credentials off home.

## Recurring cost without credits

S3 Standard Mumbai storage is **$0.025/GB-month**: 1 GB retained is $0.025/month, 10 GB is $0.25.
PUT/COPY/POST/LIST are $0.005 per 1,000 requests; GET is $0.004 per 10,000. For example,
720 single-object hourly PUTs plus 30 daily PUTs are $0.00375/month, before manifests, lists,
recovery bundles, multipart operations and downloads. These are illustrations, not measured
backup sizes or a final bill. Versioning can retain more data than current objects reveal.

Rates verified September 13 from the [official Mumbai price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-south-1/index.json).
No free allowance or promotional credit is assumed. Meter request/download charges and actual
retained bytes; the current 9.5 MiB allocated DB size is not an encrypted archive measurement.
SSE-S3 avoids a separate customer-managed KMS-key fee in this design. No backup expiry is
activated until restore acceptance, so review growth during that temporary retention gap.

## Next gates

1. Complete: approved bucket foundation applied and live controls verified; no production export yet.
2. Recovery-key custody selected and implemented separately; complete the remaining HM2
   identity/image/public-route/rollback/operating choices. Key proof is not an S3/DB restore.
3. HM3: encrypted production export, independent S3 download and verified private restore.
4. HM5: automatic hourly uploads, reviewed retention/version cleanup, failure notifications and
   a second restore using the automated backup. Public cutover remains a separate approval.

## Naming update — September 13, 2026

The input/output is `home_server_backup_bucket_name` (plus the matching ARN), local/resource
identifiers use `home_server`, and Terraform/test files are `home-server-backups.*`.
The earlier unapproved plan for `modelmatch-home-backups-957261948820` is superseded; use
the newly checksummed plan above for `modelmatch-home-server-backups-957261948820`.
The final explicit name was used at first creation; no state move or physical bucket migration
was required. The earlier unapproved bucket name was never created.
