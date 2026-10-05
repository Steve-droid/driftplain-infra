# HM2 backup recovery-key custody

September 13, 2026. **Selected by Steve:** AWS Secrets Manager plus a **local-only macOS
Keychain item** on this Mac. This replaces the earlier password-manager/paper-copy proposal.
The local copy is not an iCloud Passwords item. **Applied and verified:**
[custody evidence](https://github.com/Steve-droid/driftplain-infra/blob/0d428cb5a8951272723ef79a4680b59a243aac65/home-server/hm2-recovery-key-evidence.json) records separate successful decryption
from each store. Terraform applied 2 creates / 1 protective update / 0 deletes; its full
follow-up plan reports no changes. Steve approved this bounded slice for local commits on
September 13, 2026. The evidence's commit flags describe its earlier verification time.

## Ownership and locations

| Asset | Location / owner |
|---|---|
| Private age identity v1, primary | AWS Secrets Manager `modelmatch/home-server/recovery-key-v1`, `ap-south-1`, account `957261948820`; operator `arn:aws:iam::957261948820:user/steve` |
| Same identity, offline-capable copy | This Mac's `/Users/steve/Library/Keychains/login.keychain-db`; generic password service `modelmatch/home-server/recovery-key-v1`, account `steve` |
| Public encryption recipient | [recovery-key-v1.recipient](../recovery-key-v1.recipient); safe to store in Git and later install on the home server |
| Custody operations | [recovery-key.py](../recovery-key.py), run only by the operator on the Mac |

An age **recipient** is the public key used to encrypt; its matching **identity** is the
private key required to decrypt. Home-server backup jobs will hold only the recipient.
They must not retrieve the recovery secret. The AWS secret is separate from `modelmatch/app`.
Existing application credentials and identities are unchanged.

The Mac copy can be read with the login keychain unlocked, without an AWS/network call.
It shares the Mac's hardware and login-password fate; it is not removable offline media.
AWS backup data and the primary decryption key share one AWS account. The local copy gives
another key-recovery path, not protection against an account administrator accessing both.
Keep AWS account/MFA recovery usable if the Mac is lost; Steve owns those recovery arrangements.

## Protection and cost

Terraform owns the secret metadata and policy in
[home-server-recovery-key.tf](../../bootstrap/home-server-recovery-key.tf), with `prevent_destroy`,
a 30-day deletion-recovery window and public-policy blocking. A resource-policy deny prevents
reads by principals other than the selected operator. Both the resource policy and the
teardown role's identity guardrail deny that role all operations on the secret.
Ordinary account administrators can change policies; no immutability guarantee is claimed.

The private value is inserted separately, outside Terraform. There is no secret-version
resource/value data source, key in tfvars/state, private-key file, clipboard transfer or
secret in process arguments. The Mac helper uses Apple's native file-keychain APIs and the
explicit local login keychain, with its default creator access control. It does not allow
all applications, unlock the keychain, synchronize to iCloud or overwrite existing items.

One additional Secrets Manager secret is approximately **USD 0.40/month**, plus
**USD 0.05 per 10,000 API calls**, without credits. The default AWS-managed
`aws/secretsmanager` KMS key avoids a separate customer-managed key fee. Hourly encryption
does not retrieve this secret: only the public recipient is needed.
[AWS pricing](https://aws.amazon.com/secrets-manager/pricing/) ·
[AWS encryption](https://docs.aws.amazon.com/secretsmanager/latest/userguide/security-encryption.html).

## Prepare, publish and independently verify

Prerequisite on the Mac: `age` / `age-keygen` (installed using `brew install age`).
Run from the canonical infra repository. These commands print only public metadata/results.
Do not enable shell/CLI debug tracing or print secret-store return values.

```bash
python3 home-server/recovery-key.py prepare \
  --recipient-file home-server/recovery-key-v1.recipient \
  --secret-id modelmatch/home-server/recovery-key-v1 \
  --keychain /Users/steve/Library/Keychains/login.keychain-db --account steve
```

`prepare` generates in memory, adds the private identity to Keychain, verifies readback,
then saves only the public recipient. A retry uses the existing key; it never rotates it.
If the public recipient exists but the local identity is missing, stop and recover the
original from AWS. Do not generate a replacement that makes retained backups unreadable.

After the protected Terraform metadata has been applied under the selected AWS custody scope:

```bash
python3 home-server/recovery-key.py publish \
  --recipient-file home-server/recovery-key-v1.recipient \
  --secret-id modelmatch/home-server/recovery-key-v1 \
  --keychain /Users/steve/Library/Keychains/login.keychain-db --account steve \
  --profile saa --region ap-south-1 \
  --operator-arn arn:aws:iam::957261948820:user/steve

python3 home-server/recovery-key.py verify --source keychain \
  --recipient-file home-server/recovery-key-v1.recipient \
  --secret-id modelmatch/home-server/recovery-key-v1 \
  --keychain /Users/steve/Library/Keychains/login.keychain-db --account steve

python3 home-server/recovery-key.py verify --source aws \
  --recipient-file home-server/recovery-key-v1.recipient \
  --secret-id modelmatch/home-server/recovery-key-v1 \
  --profile saa --region ap-south-1 \
  --operator-arn arn:aws:iam::957261948820:user/steve
```

`publish` checks the operator identity and refuses a different existing AWS value. Its
stable request token makes retry after an uncertain upload idempotent. Preserve the local
Keychain item on any failure; do not regenerate. `verify --source aws` does not open Keychain,
and `verify --source keychain` makes no AWS calls. Each decrypts its own new disposable
challenge and checks exact equality. This tests key custody, not a PostgreSQL/S3 restore.

Native Keychain operations fail instead of prompting for a password. If locked or access
is denied, unlock/authorize the item locally in Keychain Access, then retry. Never paste
the Mac password or recovery identity into chat. To locate it manually, open Keychain Access,
select the **login** keychain and search the exact service name above.

## Recovery, loss and future rotation

For actual HM3 restoration, retrieve one identity into the restore process securely; pass
it to age through a pipe and write decrypted data only into the controlled restoration
environment. Do not invoke `get-secret-value` or a Keychain password-printing command in
a captured terminal. Preserve the backup manifest's recipient/version mapping.

If AWS is unavailable, use the Mac Keychain path. If the Mac is lost, recover AWS access and
retrieve the primary key onto a trusted replacement; its fresh local custody must be verified.
If both copies are lost, encrypted backups are unrecoverable.

Do not enable automatic secret rotation: old backups still require their original private
key. A deliberate future rotation uses `recovery-key-v2` in both stores and a new recipient;
keep v1 until no retained backup or recovery bundle needs it. Deletion needs explicit review.
Test each key's recoverability monthly alongside the planned private restore. Neither the
home uploader nor the Bedrock workload receives access to these private identities.

Tool semantics: [age upstream](https://github.com/FiloSottile/age) ·
[Apple Keychain API](https://developer.apple.com/documentation/security/secpassword).
