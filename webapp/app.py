"""知识库 Web 前端 — Flask 后端"""
import json
import os
import re
import sys
from pathlib import Path

import markdown
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
KB = Path(__file__).resolve().parent.parent  # knowledge/ 根目录
VECTORDB_DIR = Path.home() / "worklab" / "mcp-server" / "vectordb"
COLLECTION_NAME = "knowledge"

# Add mcp-server to path so we can reuse its venv's packages if needed
MCP_SERVER_DIR = Path.home() / "worklab" / "mcp-server"

# Lazy-loaded ChromaDB + embedding model
_chroma_client = None
_embed_model = None


def _get_chroma():
    global _chroma_client
    if _chroma_client is None:
        try:
            import chromadb
            _chroma_client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
        except ImportError:
            return None
    return _chroma_client


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer("BAAI/bge-m3", device="cpu")
        except ImportError:
            return None
    return _embed_model


def search_semantic(query, top_k=10):
    """语义搜索，返回格式化结果。如果 ChromaDB 不可用则返回 None。"""
    client = _get_chroma()
    model = _get_embed_model()
    if not client or not model:
        return None

    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        return None

    embedding = model.encode([query], normalize_embeddings=True, batch_size=1).tolist()
    results = collection.query(
        query_embeddings=embedding,
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"][0]:
        return []

    output = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        similarity = round(1 - distance, 3)
        text = results["documents"][0][i]

        # Find matching lines for compatibility with frontend
        matches = []
        query_lower = query.lower()
        for ln, line in enumerate(text.splitlines(), 1):
            if query_lower in line.lower():
                matches.append({"line": ln, "text": line.strip()})
                if len(matches) >= 3:
                    break
        # If no exact match, show first meaningful lines
        if not matches:
            for ln, line in enumerate(text.splitlines(), 1):
                line_s = line.strip()
                if line_s and not line_s.startswith("#"):
                    matches.append({"line": ln, "text": line_s})
                    if len(matches) >= 2:
                        break

        output.append({
            "file": meta.get("source_file", ""),
            "matches": matches,
            "similarity": similarity,
            "entity": meta.get("entity_name", ""),
            "type": meta.get("type", ""),
        })

    return output


# ── helpers ──────────────────────────────────────────────

def md_to_html(text):
    return markdown.markdown(text, extensions=["tables", "fenced_code", "nl2br"])


def parse_people():
    """解析 people.md，返回 [{name, fields, raw}]"""
    text = (KB / "people.md").read_text(encoding="utf-8")
    entries = []
    for block in re.split(r"(?=^## )", text, flags=re.M):
        m = re.match(r"^## (.+)", block)
        if not m:
            continue
        name = m.group(1).strip()
        fields = {}
        for fm in re.finditer(r"- \*\*(.+?)\*\*：(.+)", block):
            fields[fm.group(1)] = fm.group(2).strip()
        entries.append({"name": name, "fields": fields, "raw": block.strip()})
    return entries


def parse_projects():
    """解析 projects.md，返回 [{name, fields, raw}]"""
    text = (KB / "projects.md").read_text(encoding="utf-8")
    entries = []
    for block in re.split(r"(?=^## )", text, flags=re.M):
        m = re.match(r"^## (.+)", block)
        if not m:
            continue
        name = m.group(1).strip()
        fields = {}
        for fm in re.finditer(r"- \*\*(.+?)\*\*：(.+)", block):
            fields[fm.group(1)] = fm.group(2).strip()
        entries.append({"name": name, "fields": fields, "raw": block.strip()})
    return entries


def list_companies():
    """扫描 companies/ 目录"""
    companies_dir = KB / "companies"
    result = []
    if not companies_dir.exists():
        return result
    for d in sorted(companies_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            readme = d / "README.md"
            summary = ""
            if readme.exists():
                text = readme.read_text(encoding="utf-8")
                # 提取第一行作为标题
                for line in text.splitlines():
                    if line.startswith("# "):
                        summary = line[2:].strip()
                        break
            result.append({"name": d.name, "title": summary or d.name})
    return result


def list_meetings():
    """扫描 meetings/ 目录"""
    meetings_dir = KB / "meetings"
    result = []
    if not meetings_dir.exists():
        return result
    for f in sorted(meetings_dir.glob("*.md")):
        result.append({"filename": f.stem, "name": f.stem})
    return result


def search_files(query):
    """全文搜索所有 md 文件"""
    results = []
    query_lower = query.lower()
    for md_file in KB.rglob("*.md"):
        # 排除 webapp 目录
        if "webapp" in str(md_file):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if query_lower in text.lower():
            # 找到匹配的行
            matches = []
            for i, line in enumerate(text.splitlines(), 1):
                if query_lower in line.lower():
                    matches.append({"line": i, "text": line.strip()})
                    if len(matches) >= 3:
                        break
            rel = md_file.relative_to(KB)
            results.append({
                "file": str(rel),
                "matches": matches,
            })
    return results


# ── routes ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


def _build_graph_from_chromadb():
    """Build graph.json-compatible structure from ChromaDB metadata."""
    client = _get_chroma()
    if not client:
        return None
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        return None

    all_data = collection.get(include=["metadatas"])
    if not all_data["ids"]:
        return None

    graph = {"companies": {}, "people": {}, "projects": {}, "meetings": {}}

    for meta in all_data["metadatas"]:
        companies = [c.strip() for c in meta.get("companies", "").split(",") if c.strip()]
        people = [p.strip() for p in meta.get("people", "").split(",") if p.strip()]
        projects = [p.strip() for p in meta.get("projects", "").split(",") if p.strip()]
        entity_type = meta.get("type", "")
        entity_name = meta.get("entity_name", "")

        # Build company nodes
        for c in companies:
            if c not in graph["companies"]:
                graph["companies"][c] = {"people": [], "projects": [], "meetings": []}
            for p in people:
                if p not in graph["companies"][c]["people"]:
                    graph["companies"][c]["people"].append(p)
            for p in projects:
                if p not in graph["companies"][c]["projects"]:
                    graph["companies"][c]["projects"].append(p)

        # Build people nodes
        if entity_type == "person" and entity_name:
            if entity_name not in graph["people"]:
                graph["people"][entity_name] = {"company": "", "projects": [], "meetings": []}
            if companies:
                graph["people"][entity_name]["company"] = companies[0]
            for p in projects:
                if p not in graph["people"][entity_name]["projects"]:
                    graph["people"][entity_name]["projects"].append(p)

        # Build project nodes
        if entity_type == "project" and entity_name:
            if entity_name not in graph["projects"]:
                graph["projects"][entity_name] = {"companies": [], "people": [], "meetings": []}
            for c in companies:
                if c not in graph["projects"][entity_name]["companies"]:
                    graph["projects"][entity_name]["companies"].append(c)
            for p in people:
                if p not in graph["projects"][entity_name]["people"]:
                    graph["projects"][entity_name]["people"].append(p)

        # Build meeting nodes
        if entity_type == "meeting" and entity_name:
            if entity_name not in graph["meetings"]:
                graph["meetings"][entity_name] = {"companies": [], "people": [], "projects": []}
            for c in companies:
                if c not in graph["meetings"][entity_name]["companies"]:
                    graph["meetings"][entity_name]["companies"].append(c)
            for p in people:
                if p not in graph["meetings"][entity_name]["people"]:
                    graph["meetings"][entity_name]["people"].append(p)
            for p in projects:
                if p not in graph["meetings"][entity_name]["projects"]:
                    graph["meetings"][entity_name]["projects"].append(p)

    return graph


@app.route("/api/graph")
def api_graph():
    # Try ChromaDB first, fallback to graph.json
    graph = _build_graph_from_chromadb()
    if graph:
        return jsonify(graph)
    graph_file = KB / "graph.json"
    if graph_file.exists():
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        return jsonify(data)
    return jsonify({"companies": {}, "people": {}, "projects": {}, "meetings": {}})


@app.route("/api/stats")
def api_stats():
    return jsonify({
        "companies": len(list_companies()),
        "people": len(parse_people()),
        "projects": len(parse_projects()),
        "meetings": len(list_meetings()),
    })


@app.route("/api/system")
def api_system():
    return jsonify({
        "model": {"name": "Claude Opus 4.6", "context": "1M tokens", "id": "claude-opus-4-6[1m]"},
        "mcp_server": {
            "name": "worklab-tools",
            "status": "online",
            "tools": [
                {"name": "search_knowledge", "desc": "语义搜索知识库 (ChromaDB + bge-m3)", "icon": "🔍"},
                {"name": "index_knowledge", "desc": "重建向量索引", "icon": "📇"},
                {"name": "transcribe_audio", "desc": "Whisper 语音转写 + 说话人分离", "icon": "🎙️"},
                {"name": "register_voiceprint", "desc": "声纹注册与识别", "icon": "🔊"},
                {"name": "generate_image", "desc": "Gemini 文生图", "icon": "🎨"},
            ]
        },
        "skills": [
            {"name": "meeting", "desc": "录音→结构化会议纪要→知识库录入", "status": "active"},
        ],
        "services": [
            {"name": "Telegram Bot", "id": "configured via ~/.knowledge_bot.env", "status": "running", "manager": "launchd"},
            {"name": "Knowledge Web", "id": "localhost:8787", "status": "running", "manager": "flask"},
        ],
        "infra": {
            "runtime": "Python 3.12 (uv)",
            "asr": "Whisper large-v3 + pyannote",
            "hardware": "Apple M4 Local",
        }
    })


@app.route("/api/companies")
def api_companies():
    return jsonify(list_companies())


@app.route("/api/company/<path:name>")
def api_company(name):
    readme = KB / "companies" / name / "README.md"
    if not readme.exists():
        return jsonify({"error": "not found"}), 404
    raw = readme.read_text(encoding="utf-8")
    return jsonify({"name": name, "html": md_to_html(raw), "raw": raw})


@app.route("/api/people")
def api_people():
    return jsonify(parse_people())


@app.route("/api/person/<name>")
def api_person(name):
    for p in parse_people():
        if p["name"] == name or name in p["name"]:
            return jsonify({"name": p["name"], "fields": p["fields"], "html": md_to_html(p["raw"])})
    return jsonify({"error": "not found"}), 404


@app.route("/api/projects")
def api_projects():
    return jsonify(parse_projects())


@app.route("/api/project/<name>")
def api_project(name):
    for p in parse_projects():
        if p["name"] == name or name in p["name"]:
            return jsonify({"name": p["name"], "fields": p["fields"], "html": md_to_html(p["raw"])})
    return jsonify({"error": "not found"}), 404


@app.route("/api/meetings")
def api_meetings():
    return jsonify(list_meetings())


@app.route("/api/meeting/<path:name>")
def api_meeting(name):
    md_file = KB / "meetings" / f"{name}.md"
    if not md_file.exists():
        return jsonify({"error": "not found"}), 404
    raw = md_file.read_text(encoding="utf-8")
    return jsonify({"name": name, "html": md_to_html(raw), "raw": raw})


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    # Try semantic search first, fallback to full-text
    semantic_results = search_semantic(q)
    if semantic_results is not None:
        return jsonify(semantic_results)
    return jsonify(search_files(q))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787, debug=True)
