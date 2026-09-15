# Concrete NON-SECRET values for the platform (ephemeral) stack.
# Passed EXPLICITLY: `terraform -chdir=platform plan|apply -var-file=dev.tfvars`
# (we do not rely on auto-loaded terraform.tfvars / *.auto.tfvars — Roey's rule, predictability).
# Committed on purpose: nothing here is a secret. Secrets reach the cluster via Secrets Manager
# (ESO + IRSA), never through tfvars.

aws_region = "ap-south-1"

# --- VPC (P3) ---
vpc_name_prefix = "modelmatch"
vpc_cidr        = "10.0.0.0/16"
az_count        = 2    # 2 AZs -> 2 public + 2 private subnets + 2 NATs (one per AZ since P37)
subnet_newbits  = 4    # /16 + 4 = /20 subnets (4096 IPs each); 4 carved from the /16
enable_eks_tags = true # stamp kubernetes.io/role/*; cluster-name tag passed to the VPC module in P4

# --- EKS (P4) ---
cluster_name = "modelmatch"
k8s_version  = "1.36" # latest in-support (STANDARD_SUPPORT), re-verified 2026-09-06; NOT extended support

node_instance_types = ["t3a.medium"]
node_desired_size   = 3 # scaled 2->3 at P23 (E13): Elasticsearch/Kibana (EFK logging) is heavy on t3a.medium
node_min_size       = 3
node_max_size       = 3 # cap at 3
# kubelet --max-pods, paired with VPC CNI prefix delegation. Lifts the t3a.medium ENI/IP cap (17 pods)
# that monitoring + logging exhaust; real packing stays RAM-bound well below this. AWS-recommended max.
node_max_pods = 110

# API endpoint: public + private, but the public surface is narrowed to approved source IPs.
endpoint_public_access  = true
endpoint_private_access = true
# Steve's laptop public IP rotates (residential) — keep approved source IPs here as a list.
# Trade-off: stale residential IPs can be reassigned to others, so prune entries no longer used.
public_access_cidrs = [
  "5.29.66.191/32",   # 2026-09-06 (Phase 2 bring-up); June entries pruned
  "87.71.201.222/32", # 2026-09-09 (P38e deploy network) — applied out-of-band via
                      # `aws eks update-cluster-config` (Steve's one-off OK); listed here so
                      # `terraform plan` stays clean. Prune when no longer used.
  "46.210.169.119/32", # 2026-09-15 (HM3 export, personal hotspot) — applied out-of-band via
                      # `aws eks update-cluster-config` with Steve's OK; existing entries kept.
]

# --- IRSA (P7) ---
# Bedrock Nova surfaces the backend invokes (verified live in ap-south-1, 2026-06-13):
# Nova Lite via the APAC cross-region profile; Nova 2-Lite via the GLOBAL profile (NOT apac.).
bedrock_inference_profile_ids = ["apac.amazon.nova-lite-v1:0", "global.amazon.nova-2-lite-v1:0"]
bedrock_foundation_model_ids  = ["amazon.nova-lite-v1:0", "amazon.nova-2-lite-v1:0"]

# Secrets Manager path ESO (role B) reads; the -?????? suffix glob is appended in HCL.
app_secret_name = "modelmatch/app"

# ServiceAccount subjects baked into the trust policies — MUST match the future Helm charts (P12/P14).
backend_namespace       = "app"
backend_service_account = "modelmatch-backend"
eso_namespace           = "external-secrets"
eso_service_account     = "external-secrets"

# --- ArgoCD + App-of-Apps (P10) ---
# Chart versions pinned from argoproj/argo-helm (verified 2026-06-14): argo-cd 9.5.21 -> ArgoCD v3.4.3.
argocd_chart_version      = "9.5.21"
argocd_apps_chart_version = "2.0.5"
argocd_namespace          = "argocd"

# The 4 cluster namespaces Terraform owns. "app" MUST match backend_namespace above (IRSA role A subject).
kubernetes_namespaces = ["argocd", "app", "monitoring", "logging"]

# The root app syncs from the PUBLIC gitops repo over HTTPS — ArgoCD reads it anonymously, no credential.
gitops_repo_url        = "https://github.com/Steve-droid/driftplain-gitops.git"
gitops_target_revision = "main"
gitops_apps_path       = "argocd/apps"
