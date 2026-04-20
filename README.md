# 🚀 AutoQuark

<p align="center">
  <img src="https://img.shields.io/badge/Quark-Pan-FF6600?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNGRjY2MDAiIHN0cm9rZS13aWR0aD0iMiI+PHBhdGggZD0iTTEyIDJDNi40NzcgMiAyIDI2LjQ3NyAyIDEyczQuNDc3LTEwIDEwLTEwIDEwIDQuNDc3IDEwIDEwLTQuNDc3IDEwLTEwIDEwemC0xIDE0Yy0zLjMzMyAwLTYtMi42NjctNi02cyI2NjY3LTYgNi02IDYgMi42NjcgNiA2eiIgLz48L3N2Zz4=" alt="Quark" width="24">
  <img src="https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github" alt="GitHub">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="MIT License">
</p>

<p align="center">
  <strong>夸克网盘资源全自动化流水线</strong><br>
  转存 → 永久分享链接 → GitHub 发布 → Telegram 通知 → 资料站构建
</p>

<p align="center">
  <a href="#-快速开始"><img src="https://img.shields.io/badge/快速开始-5分钟内完成-52C41A?style=for-the-badge" alt="快速开始"></a>
  <a href="#-功能特性"><img src="https://img.shields.io/badge/功能特性-见下方-1890FF?style=for-the-badge" alt="功能特性"></a>
  <a href="https://github.com/xi7ang/AutoQuark/issues"><img src="https://img.shields.io/badge/反馈问题-Issues-FF4D4F?style=for-the-badge" alt="Issues"></a>
</p>

---

## 🎯 简介

**AutoQuark** 将夸克网盘资源发布全流程自动化——一条命令完成从「收到资源链接」到「永久分享链接生成 + GitHub 仓库发布 + Telegram 群通知」的全部步骤。

适用于：
- 📦 **资源站运营**：快速将夸克资源变成永久可分享链接
- 🤖 **AI 内容管理**：配合 OpenClaw Agent 实现无人值守发布
- 🔄 **批量处理**：JSON 批量导入，适配任意规模的内容库

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🔗 **永久分享链接** | 夸克转存后生成永久加密分享，不依赖原始分享有效期 |
| 🤖 **AI 自动分类** | 根据标题/简介自动推断目标仓库（movies/book/tools 等 11 个分类） |
| 🔁 **幂等设计** | 所有步骤幂等，中断可续跑，不会重复处理同一资源 |
| 📊 **批量处理** | JSON 批量导入，适配工作流 |
| 🔔 **Telegram 通知** | 3 群组同步发送图文 album |
| 🌐 **资料站构建** | 推送内容后自动触发 GitHub Actions 重建站点 |

## 🛠 支持的仓库分类

| 仓库 | 定义 | 仓库 | 定义 |
|------|------|------|------|
| `book` | 书籍 电子书 | `movies` | 影视 电影 电视剧 |
| `tools` | 软件 工具 APP | `games` | 游戏 Steam |
| `AIknowledge` | AI 大模型 提示词 | `curriculum` | 课程 教程 |
| `healthy` | 健康 健身 养生 | `self-media` | 自媒体 运营 |
| `cross-border` | 跨境电商 | `chinese-traditional` | 传统文化 |
| `auto` | 汽车 驾驶 购车 | | |

## 🚀 快速开始

### 1. 安装

```bash
git clone https://github.com/xi7ang/AutoQuark.git
cd AutoQuark
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入真实值（参考 references/setup-guide.md）
```

**必填配置：**

| 变量 | 说明 |
|------|------|
| `QUARK_PAN_TOOL_ROOT` | QuarkPanTool 仓库本地路径 |
| `MSWNLZ_ROOT` | mswnlz 内容仓库根目录 |
| `QUARK_COOKIES_FILE` | Quark cookies 文件路径 |
| `QUARK_TARGET_DIR_ID` | 资源保存目标文件夹 FID |
| `MSWNLZ_GITHUB_OWNER` | 你的 GitHub 用户名 |

### 3. 运行（单条资源速发）

```bash
python3 scripts/forward_to_publish.py \
  --repo movies \
  --input-text "请注意
🎬 大明王朝1566 4K版
📝 经典历史剧，制作精良
🏷️ 电视剧 历史 大明王朝
🔗 https://pan.quark.cn/s/xxxxxxxx"
```

### 4. 运行（批量处理）

```bash
# 1. 准备 items.json（格式见 references/items.example.json）
# 2. 批量转存 + 生成分享链接
python3 scripts/quark_batch_run.py \
  --items-json items.json \
  --out-json share_results.json

# 3. 发布到 GitHub + 发送 Telegram 通知
python3 scripts/mswnlz_publish.py \
  --month 202604 \
  --batch-json share_results.json
```

## 📂 目录结构

```
AutoQuark/
├── SKILL.md                  # OpenClaw Agent 技能定义
├── .env.example               # 配置模板
├── .gitignore                # 保护 .env 等敏感文件
│
├── scripts/
│   ├── forward_to_publish.py      # 单条全流程（最常用）
│   ├── quark_batch_run.py          # 批量转存 + 生成分享
│   ├── mswnlz_publish.py           # GitHub 发布 + Telegram 通知
│   ├── copy_promo_to_folders.py   # 推广文件复制到资源文件夹
│   ├── trigger_site_rebuild.sh    # 触发站点构建
│   ├── _common.py                 # 路径发现 + 环境变量加载
│   └── _state.py                  # 幂等状态管理
│
├── references/
│   ├── setup-guide.md       # 完整配置指南
│   ├── publish-flow.md       # 流水线步骤说明
│   └── items.example.json     # 输入格式示例
│
└── examples/
    ├── run_forward.sh        # 单条速发示例
    └── batch_run.sh           # 批量处理示例
```

## 🔒 安全说明

`.env` 文件包含真实凭据，已加入 `.gitignore`，**不要提交到 Git**。

## 📖 参考文档

- [配置指南](references/setup-guide.md) — 从零开始配置完整环境
- [流水线说明](references/publish-flow.md) — 各脚本作用与调用关系

---

<p align="center">
  <img src="https://img.shields.io/badge/MIT License-开源项目-52C41A?style=for-the-badge" alt="MIT">
  &nbsp;
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
</p>
