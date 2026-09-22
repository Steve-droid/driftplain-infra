# Driftplain infrastructure

[Project overview](https://github.com/Steve-droid/driftplain) · [Open the app](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [GitOps](https://github.com/Steve-droid/driftplain-gitops)

This repo provisions the infrastructure behind Driftplain and contains the tools for operating
it. Terraform manages AWS and Cloudflare resources. Shell and Python scripts handle home-server
setup, backups, identity renewal and recovery.

The app moved from AWS EKS to a single-node K3s cluster on an Ubuntu home server in September
2026. Cloudflare Tunnel connects the public domains to the private cluster. AWS still provides
storage and identity services; it no longer runs the application.

## What runs where

| Location | Responsibilities |
|---|---|
| Home server | K3s, the frontend and API, PostgreSQL, ArgoCD, ingress, monitoring, backups and the tunnel connector. |
| Cloudflare | Public DNS for `driftplain.dev` and the tunnel into the cluster. |
| AWS | S3 for Terraform state, database backups and ingestion sources; the backup recovery key in Secrets Manager; IAM Roles Anywhere; retained Route 53 zones; budget email alerts. |

This repo prepares the host and external services. The
[GitOps repo](https://github.com/Steve-droid/driftplain-gitops) defines what runs inside Kubernetes.

## Main directories

Each Terraform directory below has its own state and input values.

| Path | Purpose |
|---|---|
| [bootstrap](bootstrap/) | S3 buckets, backup retention, the recovery-key entry and AWS budget alerts. |
| [cloudflare](cloudflare/) | DNS records and the named tunnel. |
| [dns](dns/) | Retained Route 53 zones. Cloudflare now serves public DNS for `driftplain.dev`. |
| [home-server/identity](home-server/identity/) | IAM Roles Anywhere resources. These exchange host certificates for short-lived AWS credentials used by backup and recovery tools. |
| [home-server](home-server/) | Host setup, database restore, backup scheduling, certificate renewal, maintenance scripts and operating runbooks. |
| [home-server/backup-image](home-server/backup-image/) | Container image used by the cluster's scheduled database backup job. |
| [platform](platform/) | Terraform for the retired EKS deployment, including networking, cluster identity and ArgoCD setup. |
| [jenkins](jenkins/) | Terraform for the retired Jenkins controller on EC2. |
| [modules](modules/) | Reusable modules for the VPC, EKS, IAM roles and Jenkins controller. |
| [scripts](scripts/) | Utilities from the AWS deployment, including ingress discovery and platform teardown. |

## Operating the home server

Start with the [operations guide](home-server/HM5-OPERATIONS.md) for routine checks,
monitoring and maintenance. The main supporting guides are:

- [Host setup](home-server/README.md): prepare Ubuntu and install K3s.
- [S3 backups](home-server/S3-BACKUPS.md): encrypted backups and retention.
- [Lost-host recovery](home-server/HM5-LOST-HOST-RECOVERY.md): rebuild the server and restore its data and credentials.
- [Migration record](home-server/HM7-CUTOVER.md): move production data and public traffic from EKS.
- [Retained AWS services](home-server/HM8-RETAINED-SERVICES.md): what remains in AWS and why.

The cluster exports an encrypted database backup to S3 every hour. Operator-side jobs keep
additional recovery material backed up and manage certificate renewal. Prometheus monitors
the cluster, and a heartbeat job checks alerts and public endpoints before notifying an
external uptime monitor that the service is healthy.

The deployment depends on one machine, its power and its internet connection. Backups and
restore procedures provide recovery; there is no second server for automatic failover.

## Working with Terraform

Use Terraform and AWS credentials for the intended account. `AWS_PROFILE` selects the local
AWS profile. AWS resources are in `ap-south-1`.

Input variables have no defaults. Each root keeps concrete, non-sensitive values in
`dev.tfvars`, passed explicitly:

```bash
terraform -chdir=bootstrap init
terraform -chdir=bootstrap plan -var-file=dev.tfvars
```

State is stored in S3 with native locking and a separate key for each root. Review the plan
before applying it. The retired `platform/` and `jenkins/` roots are kept for reference;
they are not part of routine home-server operation.

## Checks

Check Terraform formatting and run the mocked backup tests:

```bash
terraform fmt -check -recursive
terraform -chdir=bootstrap init -backend=false
terraform -chdir=bootstrap test -var-file=dev.tfvars
```

The bootstrap tests check bucket protection, retention and recovery-key configuration without
creating AWS resources. The `test*.py` files in [home-server/](home-server/) cover the operating
tools. Live host checks and restore drills are documented in the runbooks.

Resource names beginning with `modelmatch` remain from the project's original name.
