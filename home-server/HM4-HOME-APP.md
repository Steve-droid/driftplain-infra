# HM4 — home GitOps and app rollout on the restored database

**Runbook + result record.** Operator-only, run from the Mac. Target = the home K3s cluster
(`driftplain-home`, strict-key `ssh home-server` + `sudo -n`, explicit kubeconfig/context) under
its ArgoCD root `home-server-root` (gitops `argocd/home-server/`). AWS production stays
authoritative and writable; public cutover is HM7. Sanitized results:
[hm4-home-app-evidence.json](hm4-home-app-evidence.json). Raw receipts and encrypted bundles stay
under `~/.local/share/driftplain/home-server-backups/` (0700/0600), never in Git.

Guardrails that apply to every step: nothing restores into or points at the EKS context; no
migrate/seed hook or image rebuild under an existing tag; `LLM_CLIENT=fake` / `BLOB_STORE=fake`
during isolated validation (no paid LLM call); no secret value, key, hash or row reaches stdout,
argv, Git or evidence; DRY_RUN=1, Roles Anywhere anchor/profiles disabled, EKS allowlist unchanged.

## What lands where

| Piece | Repo / path | Revision |
|---|---|---|
| Home platform children: `sealed-secrets` 2.20.0/0.40.0, `cert-manager` v1.20.2, F5 `nginx-ingress` 2.6.0 (**ClusterIP**), `cluster-issuers` (private CA profile) | gitops `argocd/home-server/apps/`, `charts/cluster-issuers/values-home-server.yaml` | PR #46, **v0.20.0** |
| Umbrella profile knobs: `image.registry`/`image.digest`, omit-able IRSA annotation (AWS render byte-identical) | gitops `charts/modelmatch` 0.2.0 | PR #46 |
| Sealed app credentials + `app-secrets` child | gitops `argocd/home-server/sealed/`, `apps/app-secrets.yaml` | PR #47 |
| Home umbrella values (GHCR digests, no IRSA, fake LLM/blob, private hosts) + `modelmatch` child | gitops `charts/modelmatch/values-home-server.yaml`, `apps/modelmatch.yaml` | part 2b (after the image copy) |
| Sealing-key custody tool + tests, active sealing cert | this directory: `home-server-sealing-keys.py`, `test-home-server-sealing-keys.py`, `home-server-sealing-active.crt` | infra HM4 PR |
| Durable S3 blob store adapter (`BLOB_STORE=s3`) | backend `app/blob_store.py`, `tests/test_blob_store.py` | backend PR #24 (no image cut) |
| Ingestion-prefix grant for the home Bedrock role (plan only) | `identity/main.tf`, `variables.tf`, `dev.tfvars`, tests | infra HM4 PR; apply is a separate approval |

## 1. Platform children (done — September 15, 16:55 UTC)

Merged gitops PR #46 (v0.20.0); the root synced all four new children Synced/Healthy within
one poll cycle. Resulting home facts: IngressClass `nginx` (F5 controller, Service
`nginx-ingress-controller` ClusterIP 80/443 — no LoadBalancer/NodePort, nothing on the LAN);
ClusterIssuers `home-server-selfsigned-bootstrap` → Certificate `home-server-ca` (cert-manager
namespace, 10-year ECDSA CA) → `home-server-ca` (all Ready); Sealed Secrets controller
`sealed-secrets-controller` in namespace `sealed-secrets` with one ACTIVE sealing key. CRDs 18 → 25.

## 2. Sealing-key custody (done — September 15, 16:57–16:58 UTC)

Tool: `home-server-sealing-keys.py` under the stable operator runtime
(`source ~/.local/share/driftplain/home-server-identity/operator.env`; `"$HOME_SERVER_PYTHON"`).
Tests: `"$HOME_SERVER_PYTHON" -W ignore test-home-server-sealing-keys.py` (8; the round trip
seals with a throwaway cert and recovers with `kubeseal --recovery-unseal`, proves strict scope
and wrong-key rejection). Every subprocess is bounded.

| Step | Command | Result |
|---|---|---|
| Active public cert | `fetch-cert --out home-server-sealing-active.crt` | key `sealed-secrets-keyh7gb7`, cert SHA-256 `3e6735a5…58e751`, valid to 2036-09-12 (public; committed) |
| Backup | `backup` | 1 key (active), age-encrypted to the published recipient: `sealing-keys.json.age` 7,231 B, SHA-256 `98e962f9…08a12`; public `manifest.json` 653 B; Mac copy `sealing-keys-20260915T165752Z/`; S3 `recovery/sealing-keys/20260915T165752Z/` (bundle version `uwqBYu2c4IG4MAmsVjIsskeo2i.FiXV2`, manifest version `gaZ0d0d7x2npIlNpZzBk3dLW2xU3HLNV`) |
| Seal | `seal --restore-dir restore-20260915T150323Z --identity-source aws --cert … --out-dir <gitops>/argocd/home-server/sealed` | `modelmatch-app-secrets` (Opaque, 4 keys) and `modelmatch-db-app` (basic-auth, `cnpg.io/reload`) sealed in strict scope from the original HM3 credential bundle |
| Recovery proof | `verify-recovery --receipt … --restore-dir … --identity-source aws --sealed … --sealed …` | independent download by key+version (checksums verified) → decrypt with the AWS-held recovery identity → offline `kubeseal --recovery-unseal` of both committed manifests → **type/keys/values match the originals for both**; 4.1 s |
| Adopt | `adopt --name modelmatch-db-app` | existing operator-created owner Secret annotated `sealedsecrets.bitnami.com/managed=true`; value unchanged |

Rules: the controller renews its sealing key every 30 days (chart default) — run `backup` again
after each renewal and before relying on newly sealed manifests (HM5 schedules and alerts on it);
old keys stay in the controller so old manifests still unseal; back up ALL keys, never only the
active one. The private recovery identity never leaves Secrets Manager/Keychain custody; the
only plaintext private-key material outside memory is kubeseal's temp key file during
`verify-recovery`, in a fresh 0700 directory that is zero-overwritten and removed afterwards.
Never seal Roles Anywhere leaves. Regenerate manifests with `seal`, never by hand.

## 3. Images — public GHCR by digest (pending: GitHub token scope)

Source digests (ECR, resolved read-only with `crane`; all `linux/amd64`, docker v2 manifests):
backend 1.0.24 `sha256:027d9fef…0528` (11 layers), frontend 1.0.24 `sha256:c7b61ab3…6de2` (13),
agent 1.1.3 `sha256:064446aa…67be` (10), agent-security 1.1.3 `sha256:0b1c0f73…31d2` (10).

Procedure (one-time reviewed copy; CI publication is a documented follow-up):
1. `gh auth refresh -h github.com -s read:packages,write:packages` (browser; Steve) — the current
   `gh` token has no packages scope, so nothing can be pushed yet.
2. `gh auth token | crane auth login ghcr.io -u Steve-droid --password-stdin`; ECR login with
   the operator profile (`aws ecr get-login-password | crane auth login … -u AWS --password-stdin`).
3. `crane copy <ecr>/<name>@<digest> ghcr.io/steve-droid/<name>:<tag>` for the four images —
   copies manifest + layers as-is (no rebuild); then `crane digest ghcr.io/steve-droid/<name>:<tag>`
   must equal the source digest. Record both.
4. Make each package **Public** in GitHub package settings (no API for visibility); verify an
   anonymous pull with an empty auth config: `DOCKER_CONFIG=$(mktemp -d) crane manifest ghcr.io/…@<digest>`.
5. Only then: gitops part 2b pins the four digests in `values-home-server.yaml`.

## 4. Migration policy (checked — no migration)

`alembic heads` run inside the actual backend 1.0.24 image (pulled by digest, read-only) prints
`a4b5c6d7e8f9 (head)`; the restored home database is at `a4b5c6d7e8f9` (HM3 evidence). The
1.0.24 tag's `migrations/` is identical to `main`. Therefore the home profile keeps
`migrate.enabled=false` and both seed flags false, and **no migration runs**. A future schema
change against home is an explicit, separately reviewed step — never a PostSync hook or tag bump.

## 5. Ingestion at home (adapter landed; grant planned; runtime deferred)

Backend PR #24 adds `S3BlobStore` (content-hash keys, SSE-S3, bounded reads/timeouts/retries,
put-before-catalog-commit). No new image is cut in HM4: home runs 1.0.24 with `BLOB_STORE=fake`
during isolated validation (ingestion is operator-gated and not exercised). The identity root
now grants the home **Bedrock** role `s3:GetObject`/`s3:PutObject` on
`modelmatch-ingestion-sources-957261948820/sources/*` only (no ListBucket/delete); a fresh,
complete, normally locked plan (`terraform plan -var-file=dev.tfvars`) reports **0 to add, 2 to
change, 0 to destroy** (the role policy and the profile session policy); `terraform test` 8/8.
Applying it, enabling sessions, and switching home to `BLOB_STORE=s3` happen together with the
Roles Anywhere helper image and live Bedrock — a separate approval (HM5), not HM4 "done".

## 6. Home umbrella + validation (pending part 2b)

After the digests are recorded: merge `values-home-server.yaml` + `apps/modelmatch.yaml`; wait
(bounded) for `modelmatch` Synced/Healthy; validate over an SSH port-forward to the ClusterIP
ingress with curl host mapping (`--connect-to api.home-server.driftplain.dev:443:127.0.0.1:8443`,
`--cacert` = the `home-server-ca` public cert): `/healthz`, `/readyz`, password login for an
existing user, owner isolation, CI ingest with an existing project token, savings/quality reads.
No seeds, no minted users/tokens, no writes that could be mistaken for production.

## Open items

- Steve: GitHub token packages scope, then package visibility → Public (section 3).
- After each sealing-key renewal: `backup` again (HM5 automates + alerts).
- Google sign-in at the private home hosts is not authorized in the Google client (password
  login is the HM4 check); real hostnames arrive with HM7.
