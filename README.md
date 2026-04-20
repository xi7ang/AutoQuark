# AutoQuark

夸克网盘资源全自动化流水线 — 转存、永久分享链接生成、GitHub 内容仓库自动发布、Telegram 群通知、资料站构建触发，开箱即用。

## ✨ 功能特性

- **一键速发**：转发资源消息，自动完成转存→生成分享→发布→通知全流程
- **永久链接**：夸克转存后生成永久加密分享链接，不依赖原始分享
- **AI 自动分类**：根据标题/简介自动推断目标仓库（movies/book/tools 等）
- **幂等设计**：所有步骤幂等，中断可续跑，不会重复处理
- **批量处理**：支持 JSON 批量导入，适配工作流
- **开箱即用**：`.env.example` 模板，5 分钟配置完成

## 📂 支持的仓库

`book` · `movies` · `tools` · `games` · `AIknowledge` · `curriculum` · `healthy` · `self-media` · `cross-border` · `chinese-traditional` · `auto`

## 🚀 快速开始

### 1. 安装

```bash
git clone https://github.com/YOUR_USERNAME/AutoQuark.git
cd AutoQuark
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入真实值（参考 references/setup-guide.md）
```

### 3. 运行（单条资源）

```bash
python3 scripts/forward_to_publish.py \
  --repo movies \
  --input-text "请注意
🎬 大明王朝1566 4K版
📝 经典历史剧，制作精良
🏷️ 电视剧 历史
🔗 https://pan.quark.cn/s/xxxxxxxx"
```

### 4. 运行（批量）

```bash
# 准备 items.json（格式见 references/items.example.json）
python3 scripts/quark_batch_run.py \
  --items-json items.json \
  --out-json share_results.json

python3 scripts/mswnlz_publish.py \
  --month 202604 \
  --batch-json share_results.json
```

## 📁 目录结构

```
AutoQuark/
├── .env.example          # 配置模板
├── SKILL.md              # OpenClaw Agent 技能定义
├── scripts/
│   ├── forward_to_publish.py   # 单条全流程
│   ├── quark_batch_run.py       # 批量转存+分享
│   ├── mswnlz_publish.py        # GitHub发布+Telegram通知
│   ├── copy_promo_to_folders.py # 推广文件复制
│   ├── trigger_site_rebuild.sh  # 触发站点构建
│   ├── _common.py              # 路径发现+环境变量
│   └── _state.py               # 幂等状态管理
├── references/
│   ├── setup-guide.md    # 完整配置指南
│   ├── publish-flow.md   # 流水线各步骤说明
│   └── items.example.json
└── examples/
    ├── run_forward.sh    # 单条速发示例
    └── batch_run.sh       # 批量处理示例
```

## 🔑 核心配置项

| 变量 | 说明 | 必填 |
|------|------|------|
| `QUARK_PAN_TOOL_ROOT` | QuarkPanTool 仓库路径 | ✅ |
| `MSWNLZ_ROOT` | mswnlz 内容仓库根目录 | ✅ |
| `QUARK_COOKIES_FILE` | Quark cookies 文件路径 | ✅ |
| `QUARK_TARGET_DIR_ID` | 资源保存目标文件夹 FID | ✅ |
| `MSWNLZ_GITHUB_OWNER` | 你的 GitHub 用户名 | ✅ |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 可选 |
| `MINIMAX_API_KEY` | MiniMax API Key | 可选 |

详细说明见 `.env.example`。

## 🔒 安全说明

`.env` 文件包含真实凭据，已加入 `.gitignore`，**不要提交到 Git**。

## 📖 参考文档

- [配置指南](references/setup-guide.md) — 从零开始配置完整环境
- [流水线说明](references/publish-flow.md) — 各脚本作用与调用关系

## MIT License
