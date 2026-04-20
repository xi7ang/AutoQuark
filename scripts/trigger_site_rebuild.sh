#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_WORKFLOW="${MSWNLZ_SITE_DEPLOY_WORKFLOW:-deploy.yml}"
DEPLOY_REF="${MSWNLZ_SITE_DEPLOY_REF:-main}"
DEPLOY_POLL_INTERVAL="${MSWNLZ_DEPLOY_POLL_INTERVAL:-5}"
DEPLOY_APPEAR_TIMEOUT="${MSWNLZ_DEPLOY_APPEAR_TIMEOUT:-60}"
DEPLOY_WATCH_TIMEOUT="${MSWNLZ_DEPLOY_WATCH_TIMEOUT:-900}"

# 允许脚本独立执行时自动加载 skill 本地配置
if [ -f "$SKILL_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SKILL_ROOT/.env"
  set +a
fi

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[ERROR] 缺少命令：$cmd" >&2
    exit 1
  fi
}

detect_repo_slug() {
  if [ -n "${MSWNLZ_SITE_REPO_SLUG:-}" ]; then
    printf '%s\n' "$MSWNLZ_SITE_REPO_SLUG"
    return 0
  fi

  python3 - <<'PY'
import re
import subprocess
import sys

url = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], text=True).strip()
match = re.search(r'github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?$', url)
if not match:
    print(f"[ERROR] 无法从 origin URL 解析 GitHub 仓库：{url}", file=sys.stderr)
    sys.exit(1)
print(match.group('slug'))
PY
}

find_dispatched_run() {
  local repo_slug="$1"
  local workflow="$2"
  local head_sha="$3"

  gh run list \
    --repo "$repo_slug" \
    --workflow "$workflow" \
    --limit 20 \
    --json databaseId,headSha,status,conclusion,event,url,createdAt \
  | python3 -c 'import json, sys; target = sys.argv[1]; runs = json.load(sys.stdin); \
for run in runs:\n    if run.get("headSha") == target and run.get("event") == "workflow_dispatch":\n        print(run.get("databaseId", "")); print(run.get("url", "")); print(run.get("status", "")); print(run.get("conclusion", "")); break' "$head_sha"
}

read_run_status() {
  local repo_slug="$1"
  local run_id="$2"

  gh run view "$run_id" --repo "$repo_slug" --json status,conclusion,url \
  | python3 -c 'import json, sys; obj = json.load(sys.stdin); print(obj.get("status", "")); print(obj.get("conclusion", "")); print(obj.get("url", ""))'
}

REPO_DIR="${MSWNLZ_SITE_REPO_DIR:-}"
CONTENT_ROOT="${MSWNLZ_CONTENT_ROOT:-${MSWNLZ_ROOT:-}}"

if [ -z "$REPO_DIR" ]; then
  if [ -n "${MSWNLZ_ROOT:-}" ]; then
    if [ -d "$MSWNLZ_ROOT/xi7ang.github.io" ]; then
      REPO_DIR="$MSWNLZ_ROOT/xi7ang.github.io"
    elif [ -d "$MSWNLZ_ROOT/<username>.github.io" ]; then
      REPO_DIR="$MSWNLZ_ROOT/<username>.github.io"
    elif [ -d "$MSWNLZ_ROOT/mswnlz.github.io" ]; then
      REPO_DIR="$MSWNLZ_ROOT/mswnlz.github.io"
    else
      REPO_DIR=""
    fi
  else
    REPO_DIR=""
  fi
fi

if [ -z "$REPO_DIR" ] || [ ! -d "$REPO_DIR" ]; then
  echo "[ERROR] 找不到站点仓库目录。请设置 MSWNLZ_SITE_REPO_DIR，或确保 MSWNLZ_ROOT/<username>.github.io 存在。" >&2
  exit 1
fi

if [ -z "$CONTENT_ROOT" ] || [ ! -d "$CONTENT_ROOT" ]; then
  echo "[ERROR] 找不到内容仓库根目录。请设置 MSWNLZ_CONTENT_ROOT 或 MSWNLZ_ROOT。" >&2
  exit 1
fi

require_command git
require_command python3
require_command gh

if ! gh auth status >/dev/null 2>&1; then
  echo "[ERROR] gh 未登录，无法显式触发 GitHub Actions workflow_dispatch。" >&2
  exit 1
fi

cd "$REPO_DIR"

SITE_REPO_SLUG="$(detect_repo_slug)"

git checkout "$DEPLOY_REF"
git pull --rebase

SOURCE_BASE_DIR="$CONTENT_ROOT" TARGET_DOCS_DIR="$REPO_DIR/docs" bash ./copy_content.sh

git add docs copy_content.sh
if git diff --cached --quiet; then
  git commit --allow-empty -m "chore: trigger site rebuild"
else
  git commit -m "chore: sync site content from content repos"
fi

git push origin "$DEPLOY_REF"

CURRENT_HEAD="$(git rev-parse HEAD)"
echo "[Deploy] workflow_dispatch => repo=$SITE_REPO_SLUG workflow=$DEPLOY_WORKFLOW ref=$DEPLOY_REF head=$CURRENT_HEAD"
gh workflow run "$DEPLOY_WORKFLOW" --repo "$SITE_REPO_SLUG" --ref "$DEPLOY_REF"

RUN_ID=""
RUN_URL=""
ELAPSED=0
while [ "$ELAPSED" -lt "$DEPLOY_APPEAR_TIMEOUT" ]; do
  mapfile -t RUN_INFO < <(find_dispatched_run "$SITE_REPO_SLUG" "$DEPLOY_WORKFLOW" "$CURRENT_HEAD")
  if [ -n "${RUN_INFO[0]:-}" ]; then
    RUN_ID="${RUN_INFO[0]}"
    RUN_URL="${RUN_INFO[1]:-}"
    echo "[Deploy] 已创建 run: id=$RUN_ID url=$RUN_URL"
    break
  fi
  sleep "$DEPLOY_POLL_INTERVAL"
  ELAPSED=$((ELAPSED + DEPLOY_POLL_INTERVAL))
done

if [ -z "$RUN_ID" ]; then
  echo "[ERROR] workflow_dispatch 已发送，但在 ${DEPLOY_APPEAR_TIMEOUT}s 内未观察到针对 $CURRENT_HEAD 的 deploy run。" >&2
  exit 1
fi

ELAPSED=0
while true; do
  mapfile -t STATUS_INFO < <(read_run_status "$SITE_REPO_SLUG" "$RUN_ID")
  RUN_STATUS="${STATUS_INFO[0]:-unknown}"
  RUN_CONCLUSION="${STATUS_INFO[1]:-}"
  RUN_URL="${STATUS_INFO[2]:-$RUN_URL}"
  echo "[Deploy] status=$RUN_STATUS conclusion=${RUN_CONCLUSION:-n/a} url=$RUN_URL"

  if [ "$RUN_STATUS" = "completed" ]; then
    if [ "$RUN_CONCLUSION" != "success" ]; then
      echo "[ERROR] deploy run 失败：$RUN_URL" >&2
      exit 1
    fi
    break
  fi

  if [ "$ELAPSED" -ge "$DEPLOY_WATCH_TIMEOUT" ]; then
    echo "[ERROR] deploy run 在 ${DEPLOY_WATCH_TIMEOUT}s 内未完成：$RUN_URL" >&2
    exit 1
  fi

  sleep "$DEPLOY_POLL_INTERVAL"
  ELAPSED=$((ELAPSED + DEPLOY_POLL_INTERVAL))
done

echo "Triggered rebuild via synced site push and confirmed deploy: $RUN_URL"
