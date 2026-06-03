---
name: autoquark
description: 夸克网盘资源全自动化流水线。将夸克分享链接批量转存为永久加密分享链接，AI自动分类到book/movies/tools等仓库，GitHub发布，月度索引更新，Telegram群通知，站点构建触发。用于：批量发布资源、自动化内容运营、快速将夸克网盘资源变成可分享的永久链接。触发词：夸克发布、资源速发、批量转存、自动发布。
---

# AutoQuark

夸克网盘 → 永久分享链接 → GitHub 内容仓库 → Telegram 通知，全自动幂等流水线。

## 何时使用

- 用户想做夸克资源批量转存
- 用户想为资源生成永久加密分享链接
- 用户想将资源自动分类发布到 GitHub 仓库
- 用户想发送 Telegram 汇总通知
- 用户想触发资料站重建
- 用户转发资源消息（以「请注意」开头）

## 环境要求

```
QUARK_PAN_TOOL_ROOT=/path/to/QuarkPanTool
MSWNLZ_ROOT=/path/to/mswnlz
MSWNLZ_SITE_REPO_DIR=/path/to/<username>.github.io
QUARK_COOKIES_FILE=/path/to/QuarkPanTool/config/cookies.txt
QUARK_TARGET_DIR_ID=xxxxxxxxxxxx   # 资源保存目标文件夹 FID
```

完整配置见 `.env.example`。

## 快速开始

### 单条资源速发（最常用）

```bash
# 配置好 .env 后，一条命令完成全流程
python3 scripts/forward_to_publish.py \
  --repo movies \
  --input-text "请注意
🎬 资源标题
📝 资源简介
🏷️ 标签1 标签2
🔗 https://pan.quark.cn/s/xxxxxxxx"
```

### 批量处理

```bash
# 1. 准备 items.json
# 2. 批量转存 + 生成分享链接
python3 scripts/quark_batch_run.py \
  --items-json items.json \
  --out-json share_results.json

# 3. 发布到 GitHub + 通知
python3 scripts/mswnlz_publish.py \
  --month 202604 \
  --batch-json share_results.json
```

## 仓库分类规则（AI 自动推断）

| 仓库 | 关键词 |
|------|--------|
| AIknowledge | AI、大模型、提示词、GPT |
| book | 书籍、电子书、小说 |
| movies | 影视、电影、电视剧、纪录片 |
| tools | 软件、工具、APP、插件 |
| games | 游戏、Steam、汉化 |
| curriculum | 课程、教程、培训 |
| healthy | 健康、健身、养生 |
| self-media | 自媒体、运营、涨粉 |

## 幂等机制

所有步骤幂等，基于 quark_url 的 MD5 前12位作为幂等键，存储于 `scripts/tmp_forward/{md5}/run_state.json`。中断后重跑自动续跑。

## 飞书同步（可选）

开启后，每个新资源会自动追加到飞书 Word 文档和电子表格：

- **Word 文档**：站点介绍 + 12 分类导航（emoji 装饰） + 单条资源块（名称/标签/类型/简介/链接）
- **Sheet 表格**：表头 = [资源名称、标签、类型、简介、链接、推送日期]
- **首次运行**：自动创建文档并把 URL 写回 `secrets.env`
- **幂等 / 去重**：checkpoint 跳过 + 按 quark_url 文档级去重，不重复写入
- **依赖**：`FEISHU_ENABLED=true` + `lark-cli` 已配置 + `lark-cli` 上下文检测绕过（脚本会清掉 `OPENCLAW_*` env vars）

### 配置

```bash
FEISHU_ENABLED=true
FEISHU_IDENTITY=user   # user = 当前用户 admin 权限（推荐）；bot = app 身份（需 app 发布）
FEISHU_DOC_URL=        # 空 = 首次自动创建
FEISHU_SHEET_URL=      # 空 = 首次自动创建
FEISHU_SHEET_TAB=Sheet1
```

### 手动调试

```bash
# 只初始化（不写资源）
python3 scripts/feishu_sync.py init --kind both

# 追加一条资源
python3 scripts/feishu_sync.py append \
  --title "测试" \
  --description "描述" \
  --tags "tag1 tag2" \
  --repo tools \
  --share-url "https://pan.quark.cn/s/abc" \
  --original-url "https://pan.quark.cn/s/src"
```

## 脚本说明

| 脚本 | 作用 |
|------|------|
| `forward_to_publish.py` | 单条资源全流程（转存→分享→发布→通知） |
| `quark_batch_run.py` | 批量转存 + 生成分享链接 |
| `mswnlz_publish.py` | GitHub 发布 + Telegram 通知 |
| `copy_promo_to_folders.py` | 复制推广文件到每个资源文件夹 |
| `trigger_site_rebuild.sh` | 触发站点 GitHub Actions 构建 |
| `_common.py` | 路径发现 + 环境变量加载 |
| `_state.py` | 幂等状态管理 |

## 安全要求

- **不要**把 `.env` 提交到 Git（已写入 `.gitignore`）
- **不要**在聊天里回显 Token、Cookies、私钥
- 执行 push、通知、构建前，确认用户意图
