# 流水线各步骤说明

## 完整流程（forward_to_publish.py）

| 步骤 | 脚本 | 说明 | 幂等 |
|------|------|------|------|
| 0 | OpenClaw Agent | AI 推理分类 repo | - |
| 1 | forward_to_publish.py | 解析消息，提取 title/description/tags/quark_url | ✅ |
| 2 | quark_batch_run.py | 转存 + 生成分享链接 | ✅ |
| 2.5 | _copy_source_to_dest() | 复制推广文件到资源文件夹 | ✅ |
| 3 | image_search_adapter.py | Bing 图片搜索 | ✅ |
| 4 | mswnlz_publish.py | GitHub 发布 + commit + push | ✅ |
| 5 | telegram_album_notify.py | TG 3群组发送 album | ✅ |

所有步骤幂等：每个步骤完成后立即写入 checkpoint，重跑时自动跳过已完成步骤。

## 独立脚本说明

### quark_batch_run.py
批量转存夸克分享链接到指定目录，生成永久加密分享链接。

**输入：** `items.json`
```json
[
  {"title": "资源标题", "url": "https://pan.quark.cn/s/xxxx"}
]
```

**输出：** `batch_share_results.json`，含 `share_results[]`。

### mswnlz_publish.py
将批量分享结果发布到 GitHub 内容仓库，更新月度索引文件，发送 Telegram 通知。

**前置：** 运行 `quark_batch_run.py` 生成 `batch_share_results.json`。

### copy_promo_to_folders.py
将推广文件（免责声明、保存提醒）复制到每个资源文件夹内部。

**前置：** `QUARK_PROMO_FOLDER_FID` 或 `QUARK_PROMO_FOLDER_PATH` 已配置。

### trigger_site_rebuild.sh
推送内容到站点仓库，触发 GitHub Actions 构建。

**要求：** `gh` CLI 已登录，`gh workflow run deploy.yml` 权限正常。

## 仓库定义表

| 仓库 | 定义 |
|------|------|
| AIknowledge | AI知识 大模型 提示词 智能体 |
| auto | 汽车 汽车维修 驾驶 购车 |
| book | 书籍 电子书 杂志 小说 出版 |
| chinese-traditional | 传统文化 国学 古籍 诗词 历史 |
| cross-border | 跨境电商 亚马逊 外贸 出海 |
| curriculum | 课程 教程 培训 学习 |
| edu-knowlege | 教育 教辅 试卷 学科 |
| healthy | 健康 养生 健身 饮食 营养 |
| movies | 影视 电影 电视剧 纪录片 视频 |
| self-media | 自媒体 短视频 运营 涨粉 |
| games | 游戏 单机游戏 PC游戏 Steam 汉化 |
| tools | 软件 工具 APP 插件 效率 |
