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
- **文件路径**：如 `inbox/files/方案书.pdf` → 读取内容作为上下文

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
3. topic = "项目名 汇报"

**如果是文件路径：**
1. 读取文件内容（md 直接读取，其他格式提取文本）
2. 用内容作为 context_text
3. topic = 用户输入的主题（如果没指定，从文件名推断）

**如果是主题文本：**
1. 调用 MCP `search_knowledge(query=主题)` 获取相关知识库内容
2. 用搜索结果作为 context_text

**控制 context_text 在 3000 字以内**，超出则截取最相关的部分。

### Step 2：确认大纲方向

向用户简要说明即将生成的 PPT 主题和页数，询问是否有特殊要求（如页数、侧重点）。

### Step 3：调用 MCP 工具生成 PPT

调用 MCP 工具 `generate_ppt`：

```
generate_ppt(topic="主题", context_text="上下文内容", page_count=页数)
```

工具会自动：
1. 调用 Gemini 生成大纲
2. 逐张生成幻灯片图片
3. 合并为 PDF
4. 返回 JSON 结果（含大纲、图片路径、PDF 路径）

### Step 4：交付

解析 `generate_ppt` 返回的 JSON，告知用户：

> PPT 已生成完毕（共 N 页）！
>
> 图片目录：`output/ppt-{主题}-{日期}/`
> PDF 文件：`output/ppt-{主题}-{日期}.pdf`
>
> 大纲：
> 1. [标题1]
> 2. [标题2]
> ...

如果是通过微信 Bot 触发，将 PDF 文件路径写入发送队列。

### Step 5：知识库关联（可选）

如果 PPT 与知识库中的项目/公司相关：
1. 在对应公司 README.md 的「相关资料」中补充 PPT 记录
2. 调用 MCP `upsert_knowledge()` 更新索引

## 默认页数规则

| 输入类型 | 默认页数 |
|----------|---------|
| 公司介绍 | 7 |
| 项目汇报 | 5 |
| 通用主题 | 5 |
| 用户指定页数 | 以用户为准 |

## 注意事项

- context_text 过长会导致大纲质量下降，控制在 3000 字以内
- 图片生成每张约 10-30 秒，7 页 PPT 预计 2-4 分钟
- 生成过程中不要中断，否则输出可能不完整
