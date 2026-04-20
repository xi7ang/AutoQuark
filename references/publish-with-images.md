# publish-with-images

## 目标

在 `quark-mswnlz-publisher` 正常发布资源的同时：

1. 使用 `image-crawler` 通过 **Bing** 抓取与资源名称相关的图片候选
2. 为每个资源筛选最多 3 张图片
3. 将资源信息与图片合并为 Telegram 图文相册消息发送
4. 保持主发布流程可降级：图片失败时仍可纯文本通知

## 新增脚本

- `scripts/publish_with_images.py`：总控编排脚本
- `scripts/title_keyword_utils.py`：标题清洗与图片关键词生成
- `scripts/image_search_adapter.py`：适配 `image-crawler` CLI 输出
- `scripts/telegram_album_notify.py`：Telegram 相册/文本消息发送

## 已增强脚本

- `scripts/quark_batch_run.py`
  - 输入项支持 `id`
  - `batch_share_results.json` 输出中透传 `id`
- `scripts/mswnlz_publish.py`
  - 支持 `--skip-telegram`
  - 支持 `--skip-push`
  - 支持 `--skip-rebuild`
  - 支持 `--result-json`
  - 支持 `--emit-json`

## 输入文件格式

参考：`references/items.example.json`

顶层格式：

```json
{
  "version": "1.0",
  "defaults": { ... },
  "items": [ ... ]
}
```

## 运行示例

### 1. Dry-run

```bash
python scripts/publish_with_images.py \
  --month 202604 \
  --items-json references/items.example.json \
  --result-json /tmp/publish_with_images_result.json \
  --work-dir /tmp/publish_with_images \
  --dry-run \
  --notify-mode off
```

### 2. 跳过夸克转存，基于已有 batch_json 做发布+图文通知

```bash
python scripts/publish_with_images.py \
  --month 202604 \
  --items-json references/items.example.json \
  --batch-json /path/to/batch_share_results.json \
  --skip-quark \
  --result-json /tmp/publish_with_images_result.json \
  --telegram-chat-id -1001234567890 \
  --telegram-thread-id 126
```

## 环境变量

至少确认以下变量可用：

```bash
export TELEGRAM_BOT_TOKEN="..."
export TG_GROUP_1_ID="-100..."
export TG_GROUP_1_THREAD="126"
export MSWNLZ_ROOT="/path/to/mswnlz"
export QUARK_PAN_TOOL_ROOT="/path/to/QuarkPanTool"
```

## 失败降级策略

- 图片抓取失败：退化为纯文本 Telegram 通知
- 图片抓到不足 3 张：使用现有图片发送相册
- Telegram 相册发送失败：退回 `sendMessage`
- 图片失败不阻塞发布主流程

## 结果文件

`publish_with_images.py` 会输出完整结果 JSON，包含：

- quark 转存结果
- 发布结果
- 图片抓取结果
- Telegram 发送结果
- 汇总状态
