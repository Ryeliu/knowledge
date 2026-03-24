# 声纹识别 + 会议智能转写 — 开发计划

> **状态：待开发**

---

## 目标

为知识库系统增加声纹识别能力，实现：
1. 为 people.md 中的人物注册声纹
2. 多人会议录音自动分离说话人并匹配身份
3. 转写结果自动录入知识库

---

## 新增目录结构

```
~/worklab/Sidejob/knowledge/
├── voiceprints/           # 声纹库（新增）
│   ├── 张三.npy           # 声纹向量
│   ├── 李四.npy
│   └── index.json         # 元数据（录入时间、音频时长、采样率等）
├── inbox/                 # 接收的文件暂存（新增）
│   └── *.mp3 / *.wav / ...
```

---

## 技术栈

| 组件 | 工具 | 作用 |
|------|------|------|
| 音频处理 | ffmpeg | 格式转换、切片 |
| 语音转文字 | openai-whisper（本地） | 每段语音转文字，中文支持好 |
| 说话人分离 | pyannote-audio | 区分录音中不同说话人的时间段 |
| 声纹提取 | pyannote embedding model | 从音频提取 d-vector 嵌入向量 |
| 声纹匹配 | cosine similarity（numpy） | 和库里的向量比对，阈值 >0.7 判定为同一人 |

### 需要安装的依赖

```bash
brew install ffmpeg
pip3 install openai-whisper pyannote.audio torch torchaudio numpy
```

> 注意：pyannote 模型需从 HuggingFace 下载（约 1GB），首次使用需同意许可协议。
> Python 3.9 兼容性需验证，可能需要升级 Python 或使用 venv。

---

## 功能设计

### 1. 声纹注册

**触发方式**：Telegram 发送音频文件，caption 为 `声纹 <人名>`

**流程**：
1. Bot 接收音频 → 保存到 `inbox/`
2. ffmpeg 转换为 wav（16kHz mono）
3. pyannote embedding model 提取声纹向量
4. 保存为 `voiceprints/<人名>.npy`
5. 更新 `voiceprints/index.json`
6. 回复：已录入 XX 的声纹（时长 XX 秒）

**要求**：
- 建议 20-30 秒清晰的单人说话音频
- 如果检测到多人说话，警告用户并拒绝录入
- 支持覆盖更新已有声纹

### 2. 会议录音转写

**触发方式**：Telegram 发送音频文件，caption 为空或包含 `会议`/`转写`

**流程**：
1. Bot 接收音频 → 保存到 `inbox/`
2. ffmpeg 转换为 wav
3. pyannote pipeline 进行说话人分离 → 得到 `[(start, end, speaker_id), ...]`
4. 对每个 speaker cluster 提取声纹向量
5. 和 `voiceprints/` 中的已知声纹逐一比对（cosine similarity）
   - similarity > 0.7 → 标记为对应人名
   - similarity ≤ 0.7 → 标记为 "未知说话人N"
6. whisper 对每个片段转文字
7. 合并为带说话人标签的完整文本：
   ```
   张三：我觉得这个方案可以推进……
   未知说话人1：那我们下周确认一下细节。
   李四：好的，我来跟进。
   ```
8. 将文本发送给 Claude Code（`claude -p "录入：<转写文本>"`）
9. Claude Code 按 CLAUDE.md 规则录入知识库
10. 回复用户转写结果摘要 + 识别到的说话人列表

### 3. 普通文件接收

**触发方式**：Telegram 发送文档/图片，无特殊 caption

**流程**：
1. 保存到 `inbox/`
2. 告知 Claude Code 有新文件，让它判断如何处理
3. 返回处理结果

---

## bot.py 改动清单

| 改动 | 说明 |
|------|------|
| 新增 `handle_audio()` | 处理音频消息（voice + audio） |
| 新增 `handle_document()` | 处理文档/文件 |
| 新增 `handle_photo()` | 处理图片 |
| 新增 `register_voiceprint(name, audio_path)` | 声纹注册逻辑 |
| 新增 `transcribe_meeting(audio_path)` | 会议转写逻辑（分离+匹配+转写） |
| 新增 `match_speaker(embedding)` | 声纹比对，返回人名或 None |
| 修改 `main()` | 注册新的 handler |

---

## CLAUDE.md 新增指令映射

| 我说的话 | 你要做的事 |
|---------|-----------|
| "声纹 [人名]" + 音频 | 录入该人的声纹 |
| 发送音频（无特殊指令） | 自动判断：短音频→转文字录入，长音频→会议转写 |
| "转写 [描述]" + 音频 | 会议转写并录入知识库 |

---

## voiceprints/index.json 格式

```json
{
  "张三": {
    "registered_at": "2026-03-22T10:30:00",
    "audio_duration_sec": 28.5,
    "sample_rate": 16000,
    "embedding_dim": 512,
    "source_file": "张三_sample.wav"
  }
}
```

---

## 风险与待确认

- [ ] Python 3.9 是否兼容 pyannote-audio 最新版（可能需要 >=3.10）
- [ ] pyannote HuggingFace 许可协议需手动同意（需要 HF token）
- [ ] 长录音（>1小时）的处理时间和内存占用需测试
- [ ] Telegram Bot API 文件大小限制：下载最大 20MB，如果会议录音超过需要压缩或分片
- [ ] 声纹匹配阈值 0.7 是经验值，实际环境可能需要调优
- [ ] 是否需要支持 Telegram voice message（ogg 格式）还是只支持 mp3/wav 文件
