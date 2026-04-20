#!/bin/bash
# 批量转存 + 生成分享链接示例
# 使用前：复制 .env.example 为 .env，填写真实配置值
#
# 用法：
#   # 准备 items.json（格式见 references/items.example.json）
#   bash examples/batch_run.sh items.json output.json
#
#   # 指定月份和标签
#   bash examples/batch_run.sh items.json output.json --month 202604 --label "短裤哥批次"

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

ITEMS_JSON="${1:-}"
OUT_JSON="${2:-}"

if [ -z "$ITEMS_JSON" ] || [ -z "$OUT_JSON" ]; then
  echo "用法: bash examples/batch_run.sh <items.json> <output.json> [额外参数...]"
  echo ""
  echo "示例 items.json:"
  echo '  ['
  echo '    {"title": "2025年杂志合集", "url": "https://pan.quark.cn/s/xxxx"},'
  echo '    {"title": "大明王朝1566 4K版", "url": "https://pan.quark.cn/s/yyyy"}'
  echo '  ]'
  exit 1
fi

shift 2

cd "$SKILL_DIR"

python3 scripts/quark_batch_run.py \
  --items-json "$ITEMS_JSON" \
  --out-json "$OUT_JSON" \
  "$@"
