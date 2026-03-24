"""
对话上下文管理 — Telegram/微信 Bot 共用模块

功能：
- 复用 Claude 会话（--resume），同一轮对话只启动一次 CLI
- 30 分钟无消息自动结束会话
- 会话结束时调用 Claude 生成对话纪要，保存到 chats/
- 新会话开始时加载上次对话摘要作为背景
"""

import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

SESSION_TIMEOUT = 1800  # 30 分钟
EXPIRY_CHECK_INTERVAL = 60  # 超时后额外等 60 秒再检查
MAX_ROUNDS = 10
SUMMARY_MAX_CHARS = 200  # 纪要中每条回复最多保留的字符数

log = logging.getLogger("chat_context")

KNOWLEDGE_DIR = Path.home() / "worklab" / "Sidejob" / "knowledge"
CHATS_DIR = KNOWLEDGE_DIR / "chats"
CLAUDE_BIN = str(Path.home() / ".local" / "bin" / "claude")

CHATS_DIR.mkdir(parents=True, exist_ok=True)


def _run_claude_raw(args, timeout=300):
    """执行 claude CLI 并返回 subprocess.CompletedProcess"""
    return subprocess.run(
        [CLAUDE_BIN] + args,
        cwd=str(KNOWLEDGE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class ChatSession:
    """管理单个 Bot 的对话上下文，复用 Claude 会话"""

    def __init__(self, source: str):
        """
        Args:
            source: "telegram" 或 "wechat"
        """
        self.source = source
        self.messages = []  # [(timestamp, "user"/"assistant", content)]
        self.last_active = 0
        self.session_start = 0
        self.session_id = None  # Claude CLI session ID
        self._lock = threading.Lock()
        self._timer = None

    def run_claude(self, user_msg: str, extra_prompt: str = "") -> str:
        """
        处理用户消息并调用 Claude，返回回复文本。
        同一轮对话内复用 Claude 会话（--resume）。

        Args:
            user_msg: 用户原始消息
            extra_prompt: 附加指令（如 SEND_FILE_HINT），仅首条消息发送
        """
        now = time.time()

        with self._lock:
            # 检查是否超时 → 保存上轮纪要，开启新会话
            if self.messages and self._is_expired(now):
                self._save_summary()
                self._reset()

            # 新会话开始
            is_new_session = not self.messages
            if is_new_session:
                self.session_start = now

            # 记录用户消息
            self.messages.append((now, "user", user_msg))
            self.last_active = now
            self._trim()

            # 当前会话的 session_id（可能为 None）
            sid = self.session_id

        # 构建 prompt
        if is_new_session:
            # 首条消息：加上次纪要摘要 + extra_prompt
            parts = []
            last_summary = self._load_last_summary()
            if last_summary:
                parts.append(f"[上次对话背景]\n{last_summary}\n")
            parts.append(user_msg)
            if extra_prompt:
                parts.append(extra_prompt)
            prompt = "\n".join(parts)
        else:
            # 后续消息：只发当前消息（Claude 已有上下文）
            prompt = user_msg

        # 调用 Claude
        response, new_sid = self._call_claude(prompt, sid)

        with self._lock:
            if new_sid:
                self.session_id = new_sid
            self.messages.append((time.time(), "assistant", response))
            self.last_active = time.time()

        self._schedule_expiry_check()
        log.info("Claude 回复（session=%s, 新会话=%s）: %s...",
                 self.session_id, is_new_session, response[:80])
        return response

    def _call_claude(self, prompt: str, session_id: str = None) -> tuple:
        """
        调用 Claude CLI，返回 (response_text, session_id)。
        如果有 session_id 则用 --resume 复用会话。
        """
        args = ["-p", "--output-format", "json"]
        if session_id:
            args += ["--resume", session_id]
        args.append(prompt)

        try:
            result = _run_claude_raw(args, timeout=300)
            if result.returncode != 0:
                log.error("Claude CLI 错误 (rc=%d): %s", result.returncode, result.stderr[:200])
                # 如果 resume 失败，尝试不带 resume 重试
                if session_id:
                    log.info("resume 失败，回退到新会话")
                    args_retry = ["-p", "--output-format", "json", prompt]
                    result = _run_claude_raw(args_retry, timeout=300)

            # 解析 JSON 输出
            output = result.stdout.strip()
            if output:
                data = json.loads(output)
                text = data.get("result", "")
                sid = data.get("session_id", "")
                return (text or "（无输出）", sid)
            return ("（无输出）", None)

        except json.JSONDecodeError:
            # JSON 解析失败，fallback 用原始输出
            log.warning("Claude 输出非 JSON，使用原始文本")
            return (result.stdout.strip() or "（无输出）", None)
        except subprocess.TimeoutExpired:
            return ("（处理超时）", None)
        except Exception as e:
            log.error("调用 Claude 出错: %s", e)
            return (f"（出错：{e}）", None)

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

    def _trim(self):
        """保留最近 MAX_ROUNDS 轮（每轮 = user + assistant = 2 条）"""
        max_msgs = MAX_ROUNDS * 2
        if len(self.messages) > max_msgs:
            self.messages = self.messages[-max_msgs:]

    def _save_summary(self):
        """调用 Claude 生成对话纪要并保存到 chats/"""
        if len(self.messages) < 2:
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

        # 调用 Claude 生成摘要（新进程，不复用会话）
        summary_prompt = (
            "请将以下对话整理为简要纪要（100-200字），提取关键信息点和待办事项。"
            "只输出纪要内容，不要加标题或前缀。\n\n"
            f"{dialog_text}"
        )

        try:
            result = _run_claude_raw(["-p", summary_prompt], timeout=120)
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

        files = sorted(CHATS_DIR.glob(f"*-{self.source}.md"), reverse=True)
        if not files:
            files = sorted(CHATS_DIR.glob("*.md"), reverse=True)
        if not files:
            return ""

        try:
            text = files[0].read_text(encoding="utf-8")
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
        self.session_id = None
