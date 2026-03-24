# Personal Knowledge System — 架构图提示词

绘制一张系统架构图，风格为深色科技风（dark theme），中文标注，布局清晰。

## 核心中枢

中央：Claude Code (Opus 4.6 · 1M context)
- 角色：智能调度中心，所有输入最终汇聚于此，由它执行知识录入/查询/生成

## 输入通道（左侧，3个入口）

1. **Telegram Bot**
   - launchd 开机自启
   - 接收：文字指令、文件/文档、图片、音频/语音、视频
   - 文件暂存到 inbox/（audio/ · files/）
   - 限制：下载最大 20MB

2. **手机浏览器 → 文件上传服务**（待开发）
   - Flask + HTTPS 自签名
   - 解决 Telegram 20MB 限制
   - 上传后 Bot 主动推送通知

3. **微信 iLink Bot**（待接入）
   - 等全量开放后替代/补充 Telegram
   - 通过 ClawBot 插件 + MCP 对接

## MCP 工具层（上方）

**MCP Server 1: worklab-tools**（本地，Python 3.12 + uv）
- `transcribe_audio`：Whisper 转写 + pyannote 说话人分离 + 声纹匹配
- `register_voiceprint`：注册声纹向量到 voiceprints/
- `generate_image`：Gemini 文生图

**MCP Server 2: ima-copilot**（HTTP :8081，FastMCP v2）
- `ask`：查询腾讯 IMA 知识库
- 数据源：微信公众号文章、AI 行业研报、新闻资讯
- 注意：cookie/token 会过期，需定期更新

## Skills 层（上方，与 MCP 并列）

- `/meeting`：录音文件 → Whisper 转写 → 结构化会议纪要 → 自动录入知识库
- `/archive`：扫描 inbox/files → 识别所属公司 → 确认 → 归档到 companies/ → 更新全部索引

## 本地知识库（右侧，核心数据）

```
knowledge/（~/worklab/Sidejob/knowledge/）
├── companies/（10家公司，每家 README.md + 资料文件）
├── people.md（20人档案）
├── projects.md（8个项目台账）
├── meetings/（3场会议纪要）
├── graph.json（四维关联索引：company↔person↔project↔meeting）
├── voiceprints/（声纹向量库 .npy + index.json）
├── inbox/（文件暂存：audio/ · files/ · processed/）
├── templates/（LaTeX 模板：briefing · report）
└── output/（编译好的 PDF）
```

## 输出通道（下方）

1. **Telegram Bot 回复**（文字结果、摘要、确认）
2. **Knowledge Web Dashboard**（localhost:8787）
   - Flask 后端，暗色 AI 驾驶舱风格
   - KPI 统计 · 3D 关系拓扑图 · Cmd+K 搜索
   - API: /api/stats, /api/graph, /api/companies, /api/people, /api/projects, /api/meetings, /api/search
3. **PDF 输出**（xelatex 编译，中文 PingFang SC）

## 数据流（用箭头标注）

### 录入流
```
用户(Telegram/微信/上传) → inbox/ → Claude Code 解析
→ 写入 companies/ + people.md + projects.md + meetings/
→ 更新 graph.json 关联索引
```

### 会议转写流
```
音频 → inbox/audio/ → /meeting skill 触发
→ Whisper 转写 + pyannote 分离 + 声纹匹配(voiceprints/)
→ 结构化纪要 → 录入知识库
```

### 归档流
```
文件 → inbox/files/ → /archive skill 触发
→ 自动识别公司 → 用户确认 → 移入 companies/公司名/
→ 更新 README.md + graph.json
```

### 查询流
```
用户提问 → Claude Code
→ 读 graph.json 确定关联范围 → 读具体文件 → 综合回答
→ 可同时查询 IMA 知识库获取外部资讯补充
```

### 外部资讯流
```
微信公众号/新闻 → 腾讯 IMA 知识库（云端）
→ ima-copilot MCP → Claude Code 按需检索
```
