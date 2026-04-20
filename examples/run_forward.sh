#!/bin/bash
# 单条资源速发示例
# 使用前：复制 .env.example 为 .env，填写真实配置值
#
# 用法：
#   bash examples/run_forward.sh movies
#   bash examples/run_forward.sh tools --input-file message.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 自动加载 .env
if [ -f "$SKILL_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$SKILL_DIR/.env"
  set +a
fi

REPO="${1:-tools}"
INPUT_TEXT="${2:-}"

cd "$SKILL_DIR"

if [ -n "$INPUT_TEXT" ]; then
  python3 scripts/forward_to_publish.py \
    --repo "$REPO" \
    --input-text "$INPUT_TEXT"
else
  echo "用法: bash examples/run_forward.sh <repo> \"<消息文本>\""
  echo "示例: bash examples/run_forward.sh movies \"请注意\n🎬 资源标题\n📝 简介\n🏷️ 标签\n🔗 https://pan.quark.cn/s/xxx\""
  exit 1
fi
