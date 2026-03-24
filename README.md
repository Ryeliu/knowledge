# Knowledge — AI-Powered Personal Knowledge Management System

A local-first personal knowledge management system powered by **Claude Code**. It uses natural language to manage contacts, companies, projects, and meeting notes — with semantic search, multi-channel input, and PDF generation.

## Architecture

```
Claude Code (Opus 4.6 · 1M context)
├── CLAUDE.md          ← Natural language rules that drive all operations
├── Scripts
│   ├── bot.py         ← Telegram Bot (receive messages/files → Claude Code → reply)
│   └── wechat-bot.py  ← WeChat iLink Bot (same pattern, WeChat protocol)
├── MCP Tools
│   ├── search_knowledge()    ← Semantic search (ChromaDB + bge-m3)
│   ├── index_knowledge()     ← Rebuild vector index
│   ├── transcribe_audio()    ← Whisper + pyannote speaker diarization
│   ├── register_voiceprint() ← Speaker identification
│   └── generate_image()      ← Gemini image generation
├── Skills
│   ├── /meeting       ← Audio → structured meeting notes → auto-ingest
│   └── /archive       ← inbox/ files → identify company → archive
├── Web Dashboard      ← Flask + D3.js knowledge graph visualization
└── Knowledge Base
    ├── companies/     ← One directory per company (README.md + attachments)
    └── meetings/      ← Chronological meeting notes
```

## Features

- **Natural Language Operations** — Talk to Claude Code to ingest, query, and manage knowledge. Rules defined in `CLAUDE.md`.
- **Semantic Search** — ChromaDB + BAAI/bge-m3 embeddings. Search by meaning, not just keywords. Filter by company, person, or entity type.
- **Multi-Channel Input** — Telegram Bot, WeChat iLink Bot, or direct CLI. Send text, files, audio, images, video.
- **Meeting Transcription** — Whisper large-v3 + pyannote speaker diarization + voiceprint matching. Auto-generates structured meeting notes.
- **File Archival** — Drop files into `inbox/`, run `/archive`, and they're automatically categorized and filed under the right company.
- **PDF Generation** — LaTeX templates + xelatex for professional briefings and reports (Chinese typography with PingFang SC).
- **Web Dashboard** — Dark-themed AI cockpit with knowledge graph visualization, KPI stats, and Cmd+K search.

## Quick Start

### 1. Clone and set up directory structure

```bash
git clone https://github.com/Ryeliu/knowledge.git
cd knowledge

# Create data directories (these are gitignored)
mkdir -p companies meetings inbox/{audio,files,processed} output voiceprints
```

### 2. Configure Telegram Bot (optional)

```bash
# Create credentials file
cat > ~/.knowledge_bot.env << 'EOF'
BOT_TOKEN=your_telegram_bot_token
CHAT_ID=your_telegram_chat_id
EOF

# Run the bot
python3 scripts/bot.py
```

### 3. Configure WeChat Bot (optional)

```bash
# Run and scan QR code to login
python3 scripts/wechat-bot.py
```

### 4. Set up MCP Server (for semantic search)

The MCP server lives in a separate directory. See [worklab-tools MCP](https://github.com/Ryeliu/knowledge) for setup.

Required tools: `search_knowledge`, `index_knowledge`, `transcribe_audio`, `register_voiceprint`, `generate_image`.

### 5. Launch Web Dashboard

```bash
cd webapp
python3 app.py
# Open http://localhost:8787
```

## Knowledge Base Structure

```
knowledge/
├── CLAUDE.md              # AI operation rules (the brain)
├── companies/             # One dir per company, README.md as entry point
│   └── CompanyName/
│       ├── README.md      # Company profile + contacts + projects
│       └── *.pdf          # Attachments
├── meetings/              # Meeting notes (YYYY-MM-DD-title.md)
├── inbox/                 # File staging area
│   ├── audio/             # Audio files for transcription
│   └── files/             # Documents for archival
├── scripts/               # Bot scripts
├── webapp/                # Web dashboard
├── templates/             # LaTeX templates
├── voiceprints/           # Speaker identification data
└── output/                # Generated PDFs
```

## Dependencies

- **Python 3.9+** (3.12 recommended)
- **Claude Code** (`~/.local/bin/claude`)
- **Telegram Bot**: `python-telegram-bot`
- **WeChat Bot**: `pycryptodome` (for media decryption)
- **Web Dashboard**: `flask`, `markdown`
- **Semantic Search** (MCP server): `chromadb`, `sentence-transformers` (BAAI/bge-m3)
- **Transcription** (MCP server): `openai-whisper`, `pyannote.audio`, `torch`
- **PDF Generation**: `xelatex` (MacTeX), PingFang SC font

## How It Works

The core idea: **CLAUDE.md is the brain**. It defines how Claude Code should handle every operation — ingestion, querying, PDF generation. The bots (Telegram/WeChat) are just input/output channels that pipe messages through `claude -p`.

When you send a message like "录入：今天和张三开了个会讨论了新项目", Claude Code:
1. Parses the input
2. Creates/updates the relevant company README
3. Creates a meeting note in `meetings/`
4. Rebuilds the vector index via `index_knowledge()`

Querying works the same way — ask in natural language, Claude Code uses `search_knowledge()` for semantic retrieval, reads the relevant files, and responds.

## License

MIT
