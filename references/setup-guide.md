# 配置指南

## 第一步：安装依赖

### 1. 克隆 QuarkPanTool

```bash
git clone https://github.com/your-fork/QuarkPanTool.git
cd QuarkPanTool
pip install -r requirements.txt
```

### 2. 配置 Quark Cookies

登录 [pan.quark.cn](https://pan.quark.cn)，F12 打开开发者工具 → Application → Cookies → 复制 `cookie` 字段值，保存到：

```
QuarkPanTool/config/cookies.txt
```

### 3. 克隆 mswnlz 内容仓库

```bash
git clone https://github.com/YOUR_USERNAME/mswnlz.git
cd mswnlz
# 内容仓库结构：book/ movies/ tools/ 等
```

### 4. 克隆站点仓库（如没有）

```bash
git clone https://github.com/YOUR_USERNAME/<username>.github.io.git
```

## 第二步：填写 .env

```bash
cp .env.example .env
# 编辑 .env，填入真实值
```

## 第三步：获取关键 FID

1. 打开夸克网盘，进入「资源保存目录」
2. 点击「分享」→ 复制文件夹链接，URL 中 `/s/` 后即为 FID：
   ```
   https://pan.quark.cn/s/xxxxx  →  FID = xxxxx
   ```

3. 同理获取推广文件所在文件夹 FID（可选）。

## 第四步：验证配置

```bash
# 检查 Python 环境
python3 -c "from quark import QuarkPanFileManager; print('OK')"

# 列出目标目录文件（验证 cookies + FID）
cd $QUARK_PAN_TOOL_ROOT
python3 -c "
from quark import QuarkPanFileManager
mgr = QuarkPanFileManager()
print(mgr.get_sorted_file_list(pdir_fid='YOUR_TARGET_DIR_ID', page='1', size='1'))
"
```

## 第五步：试跑

```bash
# 单条资源速发（最常用）
bash examples/run_forward.sh movies

# 批量处理
bash examples/batch_run.sh items.json output.json
```

## 常见问题

**Q: quark_batch_run.py 超时？**
A: Quark API 响应较慢属正常现象。重跑时会从断点续跑，不会重复转存。

**Q: mswnlz_publish.py commit 失败？**
A: 检查 GitHub SSH 密钥是否配置正确，`gh auth status` 是否登录。

**Q: Telegram 消息发送失败？**
A: 确认 Bot 已加入群组，且 Bot 对群组有「发送消息」权限。

**Q: 不想发 Telegram 通知？**
A: 留空 `TELEGRAM_BOT_TOKEN` 和所有 `TG_GROUP_*_ID` 即可跳过。
