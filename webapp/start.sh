#!/bin/bash
# 启动知识库 Web 前端（使用 mcp-server venv 以支持语义搜索）
cd "$(dirname "$0")"
exec ~/worklab/mcp-server/.venv/bin/python3 app.py
