# syntax=docker/dockerfile:1
# E21/HM5 — helper image for the in-cluster home-server backup CronJob
# (driftplain-gitops charts/home-server-backup). PREPARED, NOT BUILT OR PUBLISHED: building,
# scanning and publishing to GHCR by digest is a separate reviewed step, and the CronJob stays
# suspended until that digest is pinned in the chart and Roles Anywhere sessions are enabled.
#
# Everything is pinned:
#   • python:3.12-slim (Debian trixie) — pin the base by digest at the build review;
#   • postgresql-client-16 from PGDG — pg_dump MUST match the CNPG 16.10 server major so the
#     custom-format archive restores with the operator's pg_restore 16 (a 17 archive would not);
#   • age from Debian (the same tool the Mac export uses; recipient = recovery-key-v1);
#   • AWS CLI v1 1.45.24, which pins botocore 1.43.24 — the operator's tested SDK line —
#     and honours credential_process for the Roles Anywhere profile (no static keys);
#   • aws_signing_helper 1.8.5, Linux x86-64, verified against the published SHA-256
#     recorded in IDENTITY.md ("Credential refresh and image contract").
# Runs as uid/gid 10001 (hsbackup; Debian already owns the name "backup") with a read-only root filesystem (the chart mounts /work, /config,
# /identity). No entrypoint: the chart runs `/bin/sh /config/backup.sh`.
# python:3.12-slim-trixie multi-arch index, resolved September 17, 2026 (build review).
FROM python:3.12-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ARG PGDG_KEY_URL=https://www.postgresql.org/media/keys/ACCC4CF8.asc
ARG SIGNING_HELPER_URL=https://rolesanywhere.amazonaws.com/releases/1.8.5/X86_64/Linux/Amzn2023/aws_signing_helper
ARG SIGNING_HELPER_SHA256=beec9ed1c492d93db809890f16713e3556353294b823c2184ad4e891f1b2b54d
ARG AWSCLI_VERSION=1.45.24

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg age; \
    curl -fsSL "$PGDG_KEY_URL" | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    curl -fsSL "$SIGNING_HELPER_URL" -o /usr/local/bin/aws_signing_helper; \
    echo "$SIGNING_HELPER_SHA256  /usr/local/bin/aws_signing_helper" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/aws_signing_helper; \
    /usr/local/bin/aws_signing_helper version; \
    pip install --no-cache-dir "awscli==$AWSCLI_VERSION"; \
    apt-get purge -y --auto-remove gnupg curl; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 hsbackup; \
    useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin hsbackup; \
    pg_dump --version; age --version; aws --version

USER 10001:10001
WORKDIR /work
