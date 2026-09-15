#!/usr/bin/env bash
# E21/HM3 — bootstrap the minimal home ArgoCD root (operator-run from the Mac).
#
# Home has no helm binary and no Terraform-seeded root, so the operator (1) renders the
# argo-cd chart on the Mac, (2) server-side-applies it over strict-key SSH with the
# explicit K3s kubeconfig/context, (3) waits (bounded) for the control plane, then
# (4) applies the ONE root Application from Git and (5) waits (bounded) for the child
# Applications and the CNPG Cluster. Everything after the root comes from Git.
#
# Usage: home-server-argocd.sh {render|install|wait-argocd|apply-root|wait-apps|wait-cluster|status}
# Every wait prints a TIMEOUT marker and exits 1 instead of hanging.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITOPS_DIR="${GITOPS_DIR:-$HERE/../../driftplain-gitops}"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-9.5.21}"
RENDER="${RENDER:-${TMPDIR:-/tmp}/argocd-home-${ARGOCD_CHART_VERSION}.yaml}"
SSH_ALIAS="${SSH_ALIAS:-home-server}"
REMOTE_KUBECTL="sudo -n kubectl --kubeconfig /home/steve/.kube/driftplain-home.yaml --context driftplain-home --request-timeout=60s"
WAIT_ARGOCD="${WAIT_ARGOCD:-600}"
WAIT_APPS="${WAIT_APPS:-900}"
WAIT_CLUSTER="${WAIT_CLUSTER:-900}"

# Arguments are re-quoted for the remote shell so jsonpath/pipe characters survive.
rk() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_ALIAS" "$REMOTE_KUBECTL $(printf '%q ' "$@")"; }

guard_context() {
  local ctx server
  ctx="$(rk config view --minify -o jsonpath='{.contexts[0].name}')"
  server="$(rk config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
  if [[ "$ctx" != "driftplain-home" || "$server" != "https://127.0.0.1:6443" ]]; then
    echo "REFUSED: remote kube context/server is not the home loopback cluster" >&2; exit 1
  fi
}

render() {
  helm template argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" -n argocd --include-crds \
    -f "$GITOPS_DIR/argocd/home-server/argocd-values.yaml" > "$RENDER"
  echo "rendered argo-cd $ARGOCD_CHART_VERSION -> $RENDER ($(wc -c < "$RENDER" | tr -d ' ') bytes)"
}

install() {
  guard_context
  [[ -s "$RENDER" ]] || render
  rk create namespace argocd --dry-run=client -o yaml | rk apply -f -
  rk apply --server-side --field-manager=home-server-bootstrap -n argocd -f - < "$RENDER" | sort | uniq -c | sort -rn | head -5
}

wait_argocd() {
  guard_context
  local deadline=$((SECONDS + WAIT_ARGOCD)) w
  for w in deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis deploy/argocd-applicationset-controller sts/argocd-application-controller; do
    rk -n argocd rollout status "$w" --timeout="$((deadline - SECONDS > 30 ? deadline - SECONDS : 30))s" \
      || { echo "TIMEOUT: $w not ready within ${WAIT_ARGOCD}s"; exit 1; }
  done
  rk -n argocd get pods -o wide --no-headers | awk '{print $1, $2, $3}'
}

apply_root() {
  guard_context
  rk apply -n argocd -f - < "$GITOPS_DIR/argocd/home-server/root.yaml"
}

# Bounded poll: every listed Application must be Synced + Healthy.
wait_apps() {
  guard_context
  local deadline=$((SECONDS + WAIT_APPS)) line ok
  while :; do
    ok=1
    for app in home-server-root cnpg-operator modelmatch-postgres; do
      line="$(rk -n argocd get application "$app" -o jsonpath='{.status.sync.status}/{.status.health.status}/{.status.sync.revision}' 2>/dev/null || echo "absent")"
      echo "$(date -u +%H:%M:%SZ) $app $line"
      [[ "$line" == Synced/Healthy/* ]] || ok=0
    done
    [[ $ok == 1 ]] && return 0
    (( SECONDS < deadline )) || { echo "TIMEOUT: applications not Synced/Healthy within ${WAIT_APPS}s"; exit 1; }
    sleep 20
  done
}

wait_cluster() {
  guard_context
  local deadline=$((SECONDS + WAIT_CLUSTER)) line
  while :; do
    line="$(rk -n app get cluster.postgresql.cnpg.io modelmatch-postgres -o jsonpath='{.status.phase}|{.status.instances}|{.status.readyInstances}|{.status.currentPrimary}' 2>/dev/null || echo "absent")"
    echo "$(date -u +%H:%M:%SZ) cluster $line"
    [[ "$line" == "Cluster in healthy state|1|1|"* ]] && break
    (( SECONDS < deadline )) || { echo "TIMEOUT: CNPG cluster not healthy within ${WAIT_CLUSTER}s"; exit 1; }
    sleep 20
  done
  rk -n app get pvc,pv -o wide 2>/dev/null || true
}

status() {
  guard_context
  rk -n argocd get application -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision' 2>/dev/null || echo "no Applications"
  rk -n app get cluster.postgresql.cnpg.io,pods,pvc 2>/dev/null || echo "no CNPG cluster"
}

case "${1:-}" in
  render) render ;;
  install) install ;;
  wait-argocd) wait_argocd ;;
  apply-root) apply_root ;;
  wait-apps) wait_apps ;;
  wait-cluster) wait_cluster ;;
  status) status ;;
  *) echo "usage: $0 {render|install|wait-argocd|apply-root|wait-apps|wait-cluster|status}" >&2; exit 2 ;;
esac
