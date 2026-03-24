#!/usr/bin/env python3
"""
知识库 Telegram Bot
收到消息/文件 → 调用 Claude Code → 把结果发回 Telegram
"""

import os
import asyncio
import subprocess
import time
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 读取配置
env_path = Path.home() / ".knowledge_bot.env"
for line in env_path.read_text().splitlines():
    if "=" in line:
        k, v = line.strip().split("=", 1)
        os.environ[k] = v

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = int(os.environ["CHAT_ID"])
KNOWLEDGE_DIR = Path.home() / "worklab" / "Sidejob" / "knowledge"
OUTPUT_DIR    = KNOWLEDGE_DIR / "output"
INBOX_DIR     = KNOWLEDGE_DIR / "inbox"

# Claude Code 完整路径
CLAUDE_BIN = str(Path.home() / ".local" / "bin" / "claude")

# 确保 inbox 目录存在
INBOX_DIR.mkdir(parents=True, exist_ok=True)


def is_authorized(update: Update) -> bool:
    """只响应你自己的消息"""
    return update.effective_user.id == CHAT_ID


def run_claude(prompt: str) -> str:
    """在 knowledge 目录下调用 Claude Code，返回输出文本"""
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        cwd=str(KNOWLEDGE_DIR),
        capture_output=True,
        text=True,
        timeout=300
    )
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output += f"\n\n⚠️ 错误：{result.stderr.strip()}"
    return output or "（无输出）"


async def reply_long_text(message, text):
    """发送可能很长的文本，自动分片"""
    if len(text) <= 4000:
        await message.reply_text(text)
    else:
        # 分片发送
        for i in range(0, len(text), 4000):
            chunk = text[i:i+4000]
            await message.reply_text(chunk)


async def send_new_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """把 output/ 目录里最近生成的文件发送给用户"""
    if not OUTPUT_DIR.exists():
        return

    now = time.time()
    new_files = [
        f for f in OUTPUT_DIR.iterdir()
        if f.is_file() and now - f.stat().st_mtime < 60
        and not f.name.startswith(".")
    ]

    for f in new_files:
        await update.message.reply_document(
            document=open(f, "rb"),
            filename=f.name,
            caption=f"📄 {f.stem}"
        )


async def process_with_claude(update: Update, prompt: str):
    """通用：调用 Claude Code 并回复结果"""
    await update.message.reply_text("⏳ 处理中…")

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None, run_claude, prompt
        )
        await reply_long_text(update.message, response)
        await send_new_files(update, None)

    except subprocess.TimeoutExpired:
        await update.message.reply_text('⏰ 超时了，请稍后确认结果。')
    except Exception as e:
        await update.message.reply_text(f"❌ 出错了：{str(e)}")


async def download_file(file_obj, filename: str) -> Path:
    """下载 Telegram 文件到 inbox/，返回本地路径"""
    local_path = INBOX_DIR / filename
    await file_obj.download_to_drive(str(local_path))
    return local_path


# ─── 消息处理器 ───────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理纯文字消息"""
    if not is_authorized(update):
        return
    user_text = update.message.text.strip()
    await process_with_claude(update, user_text)


AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.wav', '.ogg', '.flac', '.aac', '.wma', '.opus'}

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文件/文档（包括作为文档发送的音频如 m4a）"""
    if not is_authorized(update):
        return

    doc = update.message.document
    filename = doc.file_name or f"file_{int(time.time())}"
    file_size_mb = (doc.file_size or 0) / (1024 * 1024)

    # Telegram Bot API 下载限制 20MB
    if file_size_mb > 20:
        await update.message.reply_text(
            f"⚠️ 文件 {filename} 太大（{file_size_mb:.1f}MB），"
            f"Telegram Bot API 下载上限 20MB。\n\n"
            f"替代方案：\n"
            f"1. 压缩后重新发送\n"
            f"2. 通过其他方式传到电脑上，放入 inbox/ 目录，然后发文字指令让我处理"
        )
        return

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        local_path = await download_file(tg_file, filename)
    except Exception as e:
        await update.message.reply_text(f"⚠️ 下载文件失败：{str(e)}")
        return

    caption = (update.message.caption or "").strip()
    ext = Path(filename).suffix.lower()

    if ext in AUDIO_EXTENSIONS:
        prompt = (
            f'用户发来了一个音频文件，已保存到 inbox/{filename}。'
            f'文件大小：{doc.file_size} 字节。'
            f'注意：当前尚未集成语音转文字功能，请告知用户文件已保存到 inbox/，'
            f'后续开发声纹识别和转写功能后可自动处理。'
        )
        if caption:
            prompt += f' 用户附言："{caption}"'
    else:
        prompt = (
            f'用户发来了一个文件，已保存到 inbox/{filename}。'
            f'文件大小：{doc.file_size} 字节。'
        )
        if caption:
            prompt += f' 用户附言："{caption}"'
        else:
            prompt += ' 请分析文件内容并告诉用户关键信息。如果适合录入知识库，请录入。'

    await process_with_claude(update, prompt)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理图片"""
    if not is_authorized(update):
        return

    # 取最大分辨率的图片
    photo = update.message.photo[-1]
    filename = f"photo_{int(time.time())}.jpg"
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        local_path = await download_file(tg_file, filename)
    except Exception as e:
        await update.message.reply_text(f"⚠️ 下载图片失败：{str(e)}")
        return

    caption = (update.message.caption or "").strip()
    prompt = f'用户发来了一张图片，已保存到 inbox/{filename}。'
    if caption:
        prompt += f' 用户附言："{caption}"'
    else:
        prompt += ' 请查看图片内容并告诉用户你看到了什么。如果包含有用信息，请录入知识库。'

    await process_with_claude(update, prompt)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理音频文件（mp3 等）和语音消息"""
    if not is_authorized(update):
        return

    if update.message.audio:
        audio = update.message.audio
        filename = audio.file_name or f"audio_{int(time.time())}.mp3"
        file_id = audio.file_id
    elif update.message.voice:
        voice = update.message.voice
        filename = f"voice_{int(time.time())}.ogg"
        file_id = voice.file_id
    else:
        return

    try:
        tg_file = await context.bot.get_file(file_id)
        local_path = await download_file(tg_file, filename)
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ 下载音频失败（可能超过20MB限制）：{str(e)}\n\n"
            f"可以压缩后重发，或直接传到电脑 inbox/ 目录。"
        )
        return

    caption = (update.message.caption or "").strip()
    prompt = (
        f'用户发来了一个音频文件，已保存到 inbox/{filename}。'
        f'注意：当前尚未集成语音转文字功能，请告知用户文件已保存，'
        f'后续开发声纹识别和转写功能后可自动处理。'
    )
    if caption:
        prompt += f' 用户附言："{caption}"'

    await process_with_claude(update, prompt)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理视频文件"""
    if not is_authorized(update):
        return

    video = update.message.video
    filename = video.file_name or f"video_{int(time.time())}.mp4"
    tg_file = await context.bot.get_file(video.file_id)
    local_path = await download_file(tg_file, filename)

    caption = (update.message.caption or "").strip()
    prompt = f'用户发来了一个视频文件，已保存到 inbox/{filename}。'
    if caption:
        prompt += f' 用户附言："{caption}"'
    else:
        prompt += ' 文件已保存到 inbox/，请告知用户。'

    await process_with_claude(update, prompt)


# ─── 主入口 ───────────────────────────────────────────

def main():
    print("🤖 知识库 Bot 启动中…")
    app = Application.builder().token(BOT_TOKEN).build()

    # 文字消息
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # 文件/文档
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # 图片
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # 音频（mp3 等文件 + 语音消息）
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, handle_audio))
    # 视频
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    print("✅ Bot 已启动，等待消息")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
