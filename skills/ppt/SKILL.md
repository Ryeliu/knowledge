---
name: ppt
description: 从知识库内容或主题生成 PPT 演示文稿。支持公司介绍、项目报告、会议纪要转 PPT 等场景。
argument-hint: [主题/公司名/项目名/文件路径]
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# PPT 生成 Skill

## 输入

`$ARGUMENTS` 可以是以下任意一种：

- **主题文本**：如 "AI在金融领域的应用"、"数字员工技术方案"
- **公司名**：如 "润信科技" → 自动从知识库提取公司资料作为上下文
- **项目名**：如 "银行营业厅AI自动化" → 自动提取项目和相关公司信息
- **文件路径**：如 `inbox/files/方案书.pdf` → 提取文本作为上下文

## 前提检查

开始前先检查 ppt-agent 后端是否在运行：

```bash
curl -s http://localhost:8002/ 2>/dev/null | grep -q running
```

如果未运行，提示用户：
> PPT Agent 后端未启动，请先运行：
> `cd ~/worklab/ppt-agent/backend && python3 main.py &`

## 执行流程

### Step 1：解析输入，收集上下文

根据输入类型收集 context_text：

**如果是公司名：**
1. 调用 MCP `search_knowledge(query=公司名, type="company")` 确认公司存在
2. 读取 `companies/公司名/README.md` 获取完整公司资料
3. 搜索相关会议纪要和项目信息
4. topic = "公司名 介绍"

**如果是项目名：**
1. 调用 MCP `search_knowledge(query=项目名, type="project")` 获取项目信息
2. 读取关联公司的 README.md
3. 搜索相关会议纪要
4. topic = "项目名 汇报"

**如果是文件路径：**
1. 通过 ppt-agent 上传接口提取文本：
   ```bash
   curl -s -X POST http://localhost:8002/upload/doc -F "file=@文件路径"
   ```
2. 用提取的文本作为 context_text
3. topic = 用户输入的主题（如果没指定，从文件名推断）

**如果是主题文本：**
1. 调用 MCP `search_knowledge(query=主题)` 获取相关知识库内容
2. 用搜索结果作为 context_text

**控制 context_text 在 3000 字以内**，超出则截取最相关的部分。

### Step 2：创建会话 & 生成大纲

1. 创建 PPT 会话：
   ```bash
   curl -s -X POST http://localhost:8002/session/create \
     -H "Content-Type: application/json" \
     -d '{"topic": "主题"}'
   ```
   → 获取 `session_id`

2. 生成 PPT 大纲：
   ```bash
   curl -s -X POST http://localhost:8002/ppt/plan \
     -H "Content-Type: application/json" \
     -d '{"session_id": "...", "topic": "...", "context_text": "...", "page_count": 页数}'
   ```
   → 获取 slides 数组（每个含 index, title, content_summary, visual_prompt）

3. **向用户展示大纲**，格式如下：

   > PPT 大纲（共 N 页）：
   > 1. 标题页 — [标题]
   > 2. [第二页标题] — [内容摘要]
   > 3. ...
   >
   > 确认生成？需要调整页数或内容方向请告诉我。

   等用户确认后继续。如果用户要求调整，修改参数重新调用 plan。

### Step 3：逐张生成幻灯片

遍历大纲中的每个 slide，调用图片生成：

```bash
curl -s -X POST http://localhost:8002/ppt/generate_slide \
  -H "Content-Type: application/json" \
  -d '{"session_id": "...", "slide_index": N, "prompt": "visual_prompt", "is_modification": false, "is_insertion": false}'
```

每完成一张，告知用户进度：
> 正在生成第 3/7 页：核心技术...

如果某张生成失败，重试一次。仍失败则跳过并告知用户。

### Step 4：汇总到知识库 output/

生成完成后，将幻灯片图片从 ppt-agent 存储目录复制到知识库 `output/`：

1. 创建输出目录：`output/ppt-{主题简称}-{日期}/`
2. 将所有幻灯片图片按顺序复制并重命名：
   ```bash
   cp ~/worklab/ppt-agent/backend/storage/images/{session_id}/*.png output/ppt-xxx/
   # 按 slide index 顺序重命名为 01-标题.png, 02-标题.png ...
   ```
3. 用 Python 将所有图片合并为一个 PDF 文件：
   ```bash
   python3 -c "
   from PIL import Image
   import glob, os
   imgs = sorted(glob.glob('output/ppt-xxx/*.png'))
   if imgs:
       images = [Image.open(f).convert('RGB') for f in imgs]
       images[0].save('output/ppt-xxx.pdf', save_all=True, append_images=images[1:])
   "
   ```

4. 告知用户：
   > PPT 已生成完毕（共 N 页）！
   >
   > 图片目录：`output/ppt-{主题}-{日期}/`
   > PDF 文件：`output/ppt-{主题}-{日期}.pdf`

5. 如果是通过微信 Bot 触发，将 PDF 文件路径写入发送队列

### Step 5：知识库关联（可选）

如果 PPT 与知识库中的项目/公司相关：
1. 在对应公司 README.md 的「相关资料」中补充 PPT 记录
2. 调用 MCP `upsert_knowledge()` 更新索引

## 默认页数规则

| 输入类型 | 默认页数 |
|----------|---------|
| 公司介绍 | 7 |
| 项目汇报 | 5 |
| 会议纪要转 PPT | 按议题数 + 2（首尾） |
| 通用主题 | 5 |
| 用户指定页数 | 以用户为准 |

## 注意事项

- context_text 过长会导致大纲质量下降，控制在 3000 字以内
- 图片生成每张约 10-30 秒，7 页 PPT 预计 2-4 分钟
- 生成过程中不要中断，否则会话状态可能不完整
- 如果是通过微信 Bot 触发，生成完成后把第一张幻灯片的预览图发给用户
