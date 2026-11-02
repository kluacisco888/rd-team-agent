#!/usr/bin/env bash
# One-click mock end-to-end deployment of the Strands + AgentCore PRD agent.
#
# Default mode is a READ-ONLY dry-run: it installs the local toolchain, runs the
# unit tests, runs preflight, prints the provisioning plan and builds the
# Runtime package. Nothing is created in AWS until --apply is passed.
#
# With --apply it provisions S3 + managed knowledge base (+ ingestion) +
# AgentCore Gateway + IAM roles, smoke-tests the Gateway, deploys the AgentCore
# Runtime, and generates one mock PRD end to end. Re-running is idempotent:
# existing tagged resources are reused and updated in place.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/one_click_deploy.sh --profile PROFILE --region REGION --expected-account ACCOUNT_ID [options]

Required:
  --profile NAME              AWS CLI profile to use
  --region CODE               target Region (must host a managed knowledge base)
  --expected-account ID       12-digit account; every step aborts on mismatch

Options:
  --environment ENV           development | test | production (default: development)
  --model-family NAME         opus-4-6 (default, E2E-proven) | opus-5-5
  --allow-global-inference    permit global.* inference; required in Regions with no
                              Geo profile for the chosen model (e.g. Opus 4.6 in
                              ap-northeast-1). Confirm data-residency rules first.
  --prefix NAME               resource-name prefix (default: prd-agent-mock-e2e)
  --skip-model-probe          skip the 1-token Bedrock access probe in preflight
  --apply                     create/update AWS resources and run the E2E test
  -h, --help                  show this help
EOF
}

PROFILE="" REGION="" ACCOUNT="" ENVIRONMENT="development" PREFIX="prd-agent-mock-e2e"
MODEL_FAMILY="opus-4-6" APPLY=0 GLOBAL_FLAG="" PROBE_FLAG="--probe-model"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --expected-account) ACCOUNT="$2"; shift 2 ;;
    --environment) ENVIRONMENT="$2"; shift 2 ;;
    --model-family) MODEL_FAMILY="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --allow-global-inference) GLOBAL_FLAG="--allow-global-inference"; shift ;;
    --skip-model-probe) PROBE_FLAG=""; shift ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done
if [[ -z "$PROFILE" || -z "$REGION" || -z "$ACCOUNT" ]]; then
  usage >&2
  exit 64
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/deploy/out/$REGION"
STATE="$OUT/stack-state.json"
PY="$ROOT/.venv/bin/python"
mkdir -p "$OUT"
export AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
export PYTHONPATH="$ROOT/src"
COMMON=(--profile "$PROFILE" --region "$REGION" --expected-account "$ACCOUNT")

step() { printf '\n=== [%s] %s ===\n' "$1" "$2" >&2; }
state_field() { "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$STATE" "$1"; }

step 1 "Local Python 3.12 environment"
command -v uv >/dev/null || { echo "uv not found; install from https://docs.astral.sh/uv/" >&2; exit 1; }
[[ -x "$PY" ]] || uv venv --python 3.12 .venv
uv pip install --quiet --python "$PY" -r requirements.txt

step 2 "Unit tests"
"$PY" -m unittest discover -s tests -p 'test_*.py' -q

step 3 "Preflight (read-only)"
# shellcheck disable=SC2086  # optional flags are intentionally word-split
"$PY" scripts/preflight.py "${COMMON[@]}" --model-family "$MODEL_FAMILY" $GLOBAL_FLAG $PROBE_FLAG \
  | tee "$OUT/preflight.json"

step 4 "Provisioning plan (dry-run)"
# shellcheck disable=SC2086
"$PY" scripts/provision_test_stack.py "${COMMON[@]}" --prefix "$PREFIX" --model-family "$MODEL_FAMILY" $GLOBAL_FLAG \
  | tee "$OUT/provision-plan.json"

step 5 "Build linux/arm64 Runtime package (no AWS calls)"
"$PY" scripts/deploy_runtime.py --region "$REGION" --expected-account "$ACCOUNT" --build-only

if [[ "$APPLY" -ne 1 ]]; then
  printf '\nDRY-RUN COMPLETE. No AWS resources were created.\n' >&2
  printf 'Review %s, obtain human approval, then re-run with --apply.\n' "$OUT/provision-plan.json" >&2
  exit 0
fi

step 6 "Provision S3, managed knowledge base + ingestion, Gateway, IAM roles"
# shellcheck disable=SC2086
"$PY" scripts/provision_test_stack.py "${COMMON[@]}" --prefix "$PREFIX" --model-family "$MODEL_FAMILY" $GLOBAL_FLAG \
  --apply --state-file "$STATE" > "$OUT/provision-apply.log"
echo "state: $STATE" >&2

step 7 "Gateway smoke test (SigV4 -> Gateway -> managed KB)"
"$PY" scripts/smoke_gateway.py --region "$REGION" \
  --url "$(state_field gateway_url)" --tool "$(state_field gateway_tool_name)" \
  --query "What is the maximum CSV row count and is XLSX supported?" \
  > "$OUT/gateway-smoke.json"
echo "gateway smoke: success" >&2

step 8 "Deploy AgentCore Runtime (direct code deployment)"
"$PY" scripts/deploy_runtime.py "${COMMON[@]}" --state-file "$STATE" \
  --environment "$ENVIRONMENT" --apply > "$OUT/runtime-deploy.log"
echo "runtime: $(state_field runtime_arn)" >&2

step 9 "End-to-end mock PRD generation (typically 8-15 minutes)"
set +e
"$PY" scripts/invoke_runtime.py "${COMMON[@]}" --state-file "$STATE" --out-dir "$OUT/runs" \
  | tee "$OUT/e2e-latest.json"
rc=${PIPESTATUS[0]}
set -e
case "$rc" in
  0) printf '\nE2E PASS: PRD completed and validation passed. Evidence under %s/runs/\n' "$OUT" >&2 ;;
  2) printf '\nE2E STOPPED: the agent asked clarification questions (see e2e-latest.json).\n' >&2 ;;
  *) printf '\nE2E FAIL (exit %s). See e2e-latest.json and CloudWatch Runtime logs.\n' "$rc" >&2 ;;
esac
exit "$rc"
