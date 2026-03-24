"""
对话上下文管理 — Telegram/微信 Bot 共用模块

功能：
- 维护当前会话的对话历史（内存）
- 30 分钟无消息自动结束会话
- 会话结束时调用 Claude 生成对话纪要，保存到 chats/
- 新会话开始时加载上次对话摘要作为背景
"""

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

SESSION_TIMEOUT = 1800  # 30 分钟
EXPIRY_CHECK_INTERVAL = 60  # 每 60 秒检查一次超时
MAX_ROUNDS = 10
SUMMARY_MAX_CHARS = 200  # 历史中每条回复最多保留的字符数

log = logging.getLogger("chat_context")

KNOWLEDGE_DIR = Path.home() / "worklab" / "Sidejob" / "knowledge"
CHATS_DIR = KNOWLEDGE_DIR / "chats"
CLAUDE_BIN = str(Path.home() / ".local" / "bin" / "claude")

CHATS_DIR.mkdir(parents=True, exist_ok=True)


class ChatSession:
    """管理单个 Bot 的对话上下文"""

    def __init__(self, source: str):
        """
        Args:
            source: "telegram" 或 "wechat"
        """
        self.source = source
        self.messages = []  # [(timestamp, "user"/"assistant", content)]
        self.last_active = 0
        self.session_start = 0
        self._lock = threading.Lock()
        self._timer = None

    def on_message(self, user_msg: str) -> str:
        """
        处理新消息，返回带上下文的 prompt。
        如果会话超时，先保存纪要再开启新会话。
        """
        now = time.time()

        with self._lock:
            # 检查是否超时 → 保存上轮纪要，开启新会话
            if self.messages and self._is_expired(now):
                self._save_summary()
                self._reset()

            # 新会话开始
            if not self.messages:
                self.session_start = now

            # 构建带上下文的 prompt
            prompt = self._build_prompt(user_msg)

            # 记录用户消息
            self.messages.append((now, "user", user_msg))
            self.last_active = now

            # 滚动丢弃超过 MAX_ROUNDS 的旧消息（一轮 = user + assistant）
            self._trim()

        self._schedule_expiry_check()
        return prompt

    def on_response(self, response: str):
        """记录 Claude 的回复"""
        now = time.time()
        with self._lock:
            self.messages.append((now, "assistant", response))
            self.last_active = now

    def _schedule_expiry_check(self):
        """启动后台定时器，超时后自动保存纪要"""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(
            SESSION_TIMEOUT + EXPIRY_CHECK_INTERVAL,
            self._auto_expire,
        )
        self._timer.daemon = True
        self._timer.start()

    def _auto_expire(self):
        """后台定时器回调：检查超时并保存纪要"""
        with self._lock:
            if self.messages and self._is_expired(time.time()):
                log.info("会话超时，自动生成纪要（%d 条消息）", len(self.messages))
                self._save_summary()
                self._reset()
                log.info("纪要已保存")

    def _is_expired(self, now: float) -> bool:
        return self.last_active > 0 and (now - self.last_active) > SESSION_TIMEOUT

    def _build_prompt(self, current_msg: str) -> str:
        """拼接历史对话 + 上次纪要 + 当前消息"""
        parts = []

        # 如果是新会话，加载上次对话摘要
        if not self.messages:
            last_summary = self._load_last_summary()
            if last_summary:
                parts.append(f"[上次对话背景]\n{last_summary}\n")

        # 拼接当前会话历史
        if self.messages:
            history_lines = []
            for _, role, content in self.messages:
                if role == "user":
                    history_lines.append(f"用户：{content}")
                else:
                    # 回复只保留摘要
                    truncated = content[:SUMMARY_MAX_CHARS]
                    if len(content) > SUMMARY_MAX_CHARS:
                        truncated += "..."
                    history_lines.append(f"助手：{truncated}")
            parts.append("[对话上下文]\n" + "\n".join(history_lines) + "\n")

        parts.append(f"[当前消息]\n{current_msg}")

        return "\n".join(parts)

    def _trim(self):
        """保留最近 MAX_ROUNDS 轮（每轮 = user + assistant = 2 条）"""
        max_msgs = MAX_ROUNDS * 2
        if len(self.messages) > max_msgs:
            self.messages = self.messages[-max_msgs:]

    def _save_summary(self):
        """调用 Claude 生成对话纪要并保存到 chats/"""
        if len(self.messages) < 2:
            # 只有一条消息，不值得生成纪要
            return

        # 拼接完整对话
        dialog_lines = []
        for _, role, content in self.messages:
            prefix = "用户" if role == "user" else "助手"
            dialog_lines.append(f"{prefix}：{content}")
        dialog_text = "\n".join(dialog_lines)

        # 计算时间范围
        start_time = datetime.fromtimestamp(self.session_start)
        end_time = datetime.fromtimestamp(self.last_active)
        rounds = sum(1 for _, r, _ in self.messages if r == "user")

        # 调用 Claude 生成摘要
        summary_prompt = (
            "请将以下对话整理为简要纪要（100-200字），提取关键信息点和待办事项。"
            "只输出纪要内容，不要加标题或前缀。\n\n"
            f"{dialog_text}"
        )

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", summary_prompt],
                cwd=str(KNOWLEDGE_DIR),
                capture_output=True,
                text=True,
                timeout=120,
            )
            summary = result.stdout.strip() or "（摘要生成失败）"
        except Exception:
            summary = "（摘要生成超时）"

        # 保存文件
        filename = f"{start_time.strftime('%Y-%m-%d-%H%M%S')}-{self.source}.md"
        filepath = CHATS_DIR / filename

        content = (
            f"# 对话纪要\n\n"
            f"- **时间**：{start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%H:%M')}\n"
            f"- **来源**：{self.source}\n"
            f"- **轮数**：{rounds}\n\n"
            f"## 摘要\n\n{summary}\n\n"
            f"## 原始对话\n\n"
        )
        for _, role, msg in self.messages:
            prefix = "用户" if role == "user" else "助手"
            content += f"**{prefix}**：{msg}\n\n"

        filepath.write_text(content, encoding="utf-8")

    def _load_last_summary(self) -> str:
        """加载最近一条对话纪要的摘要部分"""
        if not CHATS_DIR.exists():
            return ""

        # 找到最近的纪要文件（按文件名倒序）
        files = sorted(CHATS_DIR.glob(f"*-{self.source}.md"), reverse=True)
        if not files:
            # 也尝试加载另一个 bot 的纪要
            files = sorted(CHATS_DIR.glob("*.md"), reverse=True)
        if not files:
            return ""

        try:
            text = files[0].read_text(encoding="utf-8")
            # 提取 ## 摘要 后面的内容
            match = re.search(r"## 摘要\s*\n\n(.+?)(?=\n## |\Z)", text, re.DOTALL)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
        return ""

    def _reset(self):
        """重置会话状态"""
        self.messages = []
        self.last_active = 0
        self.session_start = 0


# 需要 import re
import re
