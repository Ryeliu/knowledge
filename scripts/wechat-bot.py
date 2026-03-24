#!/usr/bin/env python3
"""
知识库微信 Bot（iLink API）
收到消息/文件 → 调用 Claude Code → 把结果发回微信

使用 curl 做 HTTP 请求（绕过 Python 3.9 SSL 兼容问题）
"""

import os
import json
import time
import base64
import struct
import subprocess
import uuid
import logging
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from chat_context import ChatSession

# ─── 日志 ───────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "wechat-bot.log"),
    ],
)
log = logging.getLogger("wechat-bot")

# ─── 配置 ───────────────────────────────────────────────

ILINK_BASE = "https://ilinkai.weixin.qq.com"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
ENV_PATH = Path.home() / ".wechat_bot.env"
CURSOR_PATH = Path.home() / ".wechat_bot_cursor"
KNOWLEDGE_DIR = Path.home() / "worklab" / "Sidejob" / "knowledge"
OUTPUT_DIR = KNOWLEDGE_DIR / "output"
INBOX_DIR = KNOWLEDGE_DIR / "inbox"
CLAUDE_BIN = str(Path.home() / ".local" / "bin" / "claude")

SEND_QUEUE_PATH = Path.home() / ".wechat_bot_send_queue.json"
LAST_CONTACT_PATH = Path.home() / ".wechat_bot_last_contact.json"

INBOX_DIR.mkdir(parents=True, exist_ok=True)
(INBOX_DIR / "audio").mkdir(exist_ok=True)
(INBOX_DIR / "files").mkdir(exist_ok=True)


def load_env():
    """从 .env 文件加载 bot_token"""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v
    return os.environ.get("WECHAT_BOT_TOKEN", "")


def save_token(token):
    """保存 bot_token 到 .env"""
    ENV_PATH.write_text(f"WECHAT_BOT_TOKEN={token}\n")
    os.environ["WECHAT_BOT_TOKEN"] = token
    log.info("Token 已保存到 %s", ENV_PATH)


def load_cursor():
    """加载消息游标"""
    if CURSOR_PATH.exists():
        return CURSOR_PATH.read_text().strip()
    return ""


def save_cursor(cursor):
    """持久化消息游标"""
    CURSOR_PATH.write_text(cursor)


# ─── HTTP 请求（curl）─────────────────────────────────────

def make_headers(token=""):
    """构造 iLink API 请求头列表（用于 curl -H）"""
    random_uin = struct.unpack("I", os.urandom(4))[0]
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "iLink-Bot-Client/1.0",
        "X-WECHAT-UIN": base64.b64encode(str(random_uin).encode()).decode(),
        "X-Request-ID": str(uuid.uuid4()),
    }
    if token:
        headers["AuthorizationType"] = "ilink_bot_token"
        headers["Authorization"] = f"Bearer {token}"
    return headers


def curl_get(url, headers=None, timeout=15):
    """用 curl 发 GET 请求，返回 JSON"""
    cmd = ["curl", "-s", "--max-time", str(timeout)]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        raise RuntimeError(f"curl GET 失败: {result.stderr}")
    return json.loads(result.stdout)


def curl_post(url, body, headers=None, timeout=15):
    """用 curl 发 POST 请求，返回 JSON"""
    cmd = ["curl", "-s", "--max-time", str(timeout), "-X", "POST"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd += ["-d", json.dumps(body)]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        raise RuntimeError(f"curl POST 失败: {result.stderr}")
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def curl_download(url, save_path, timeout=60):
    """用 curl 下载文件"""
    cmd = ["curl", "-s", "--max-time", str(timeout), "-o", str(save_path), url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        raise RuntimeError(f"curl 下载失败: {result.stderr}")


def ilink_get(token, endpoint, params=None):
    """iLink GET 请求"""
    url = f"{ILINK_BASE}/ilink/bot/{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    return curl_get(url, make_headers(token))


def ilink_post(token, endpoint, body, timeout=15):
    """iLink POST 请求"""
    url = f"{ILINK_BASE}/ilink/bot/{endpoint}"
    return curl_post(url, body, make_headers(token), timeout)


# ─── 扫码登录 ───────────────────────────────────────────

def login():
    """扫码登录，返回 bot_token"""
    log.info("正在获取登录二维码…")

    data = curl_get(
        f"{ILINK_BASE}/ilink/bot/get_bot_qrcode?bot_type=3",
        {"User-Agent": "iLink-Bot-Client/1.0"},
    )

    qr_id = data.get("qrcode", "")
    qr_url = data.get("qrcode_img_content", "")
    if not qr_id or not qr_url:
        log.error("未获取到二维码数据: %s", data)
        raise RuntimeError("获取二维码失败")

    # 终端直接显示二维码（用完整 URL）
    try:
        import qrcode as qr_lib
        qr = qr_lib.QRCode(border=1)
        qr.add_data(qr_url)
        qr.make(fit=True)
        print()
        qr.print_ascii(invert=True)
    except Exception as e:
        log.warning("生成二维码失败: %s", e)
        log.info("二维码 URL: %s", qr_url)

    print(f"\n📱 请用微信「扫一扫」扫描上方二维码\n")

    # 轮询扫码状态
    for i in range(120):
        time.sleep(1)
        try:
            status_data = curl_get(
                f"{ILINK_BASE}/ilink/bot/get_qrcode_status?qrcode={qr_id}",
                {"User-Agent": "iLink-Bot-Client/1.0"},
                timeout=20,
            )
        except Exception as e:
            log.debug("轮询状态出错: %s", e)
            continue

        status = status_data.get("status", "")
        if status == "confirmed":
            token = status_data.get("bot_token", "")
            if token:
                print("\n✅ 登录成功！")
                save_token(token)
                return token
            log.error("确认了但没有 token: %s", status_data)
            raise RuntimeError("登录异常")
        elif status == "scanned" or status == "wait_confirm":
            if i % 5 == 0:
                print("  等待确认…")
        # status == "wait" 继续等

    raise RuntimeError("登录超时（2分钟）")


# ─── 媒体下载与解密 ──────────────────────────────────────

def parse_aes_key(aes_key_b64):
    """
    解析 AES key，支持两种编码：
    - 图片：base64(raw 16 bytes)
    - 文件/语音/视频：base64(hex string 32 chars) → hex → raw 16 bytes
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            hex_str = decoded.decode("ascii")
            if all(c in "0123456789abcdefABCDEF" for c in hex_str):
                return bytes.fromhex(hex_str)
        except Exception:
            pass
    raise ValueError(f"aes_key 解码异常: 长度 {len(decoded)}，预期 16 或 32")


def decrypt_media(encrypted_data, aes_key_b64):
    """AES-128-ECB 解密媒体文件"""
    key = parse_aes_key(aes_key_b64)
    cipher = AES.new(key, AES.MODE_ECB)
    decrypted = unpad(cipher.decrypt(encrypted_data), AES.block_size)
    return decrypted


def download_media(encrypt_query_param, aes_key_b64, save_path):
    """从 CDN 下载并解密媒体文件"""
    from urllib.parse import quote
    # encrypt_query_param 直接用原始值（已经是 URL-safe 的 base64）
    url = f"{CDN_BASE}/download?encrypted_query_param={quote(encrypt_query_param, safe='')}"
    log.info("CDN 下载 URL 长度: %d", len(url))
    tmp_path = save_path.with_suffix(save_path.suffix + ".enc")
    curl_download(url, tmp_path)

    encrypted_data = tmp_path.read_bytes()
    tmp_path.unlink()

    if aes_key_b64:
        data = decrypt_media(encrypted_data, aes_key_b64)
    else:
        data = encrypted_data

    save_path.write_bytes(data)
    log.info("媒体已保存: %s (%d bytes)", save_path, len(data))
    return save_path


# ─── 消息发送 ────────────────────────────────────────────

def send_text(token, context_token, to_user_id, text):
    """发送文本消息（自动分段）"""
    max_len = 2000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if len(text) > max_len else [text]

    for chunk in chunks:
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": f"claude-wechat-{int(time.time())}-{uuid.uuid4().hex[:8]}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
            },
            "base_info": {"channel_version": "1.0.2"},
        }
        try:
            ilink_post(token, "sendmessage", body)
        except Exception as e:
            log.error("发送消息失败: %s", e)


def send_file(token, context_token, to_user_id, file_path):
    """上传文件到 CDN 并发送给用户"""
    import hashlib
    from urllib.parse import quote

    file_path = Path(file_path)
    if not file_path.exists():
        log.error("文件不存在: %s", file_path)
        return False

    plaintext = file_path.read_bytes()
    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    filekey = uuid.uuid4().hex
    aeskey_bytes = os.urandom(16)
    aeskey_hex = aeskey_bytes.hex()

    # AES-128-ECB PKCS7 加密后的大小（与官方 SDK 一致）
    import math
    padded_size = math.ceil((rawsize + 1) / 16) * 16

    # 1. 获取上传 URL
    upload_resp = ilink_post(token, "getuploadurl", {
        "filekey": filekey,
        "media_type": 3,  # FILE (UploadMediaType: IMAGE=1, VIDEO=2, FILE=3)
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": padded_size,
        "no_need_thumb": True,
        "aeskey": aeskey_hex,
        "base_info": {"channel_version": "1.0.2"},
    })

    upload_param = upload_resp.get("upload_param", "")
    if not upload_param:
        log.error("getuploadurl 未返回 upload_param: %s", upload_resp)
        return False

    # 2. 加密文件并写入临时文件
    import math
    import tempfile
    from Crypto.Util.Padding import pad
    cipher = AES.new(aeskey_bytes, AES.MODE_ECB)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))

    tmp_enc = Path(tempfile.mktemp(suffix=".enc"))
    tmp_enc.write_bytes(ciphertext)
    log.info("加密文件: %s → %s (%d bytes)", file_path.name, tmp_enc, len(ciphertext))

    # 3. 上传到 CDN
    cdn_url = f"{CDN_BASE}/upload?encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"
    tmp_headers = Path(tempfile.mktemp(suffix=".headers"))
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "120", "-X", "POST",
             "-H", "Content-Type: application/octet-stream",
             "-H", "Expect:",
             "--data-binary", f"@{tmp_enc}",
             "-D", str(tmp_headers),
             cdn_url],
            capture_output=True,
            text=True,
            timeout=130,
        )
    finally:
        tmp_enc.unlink(missing_ok=True)

    # 从响应头获取 x-encrypted-param
    headers_text = tmp_headers.read_text() if tmp_headers.exists() else ""
    tmp_headers.unlink(missing_ok=True)
    download_param = ""
    for line in headers_text.splitlines():
        if line.lower().startswith("x-encrypted-param:"):
            download_param = line.split(":", 1)[1].strip()
            break

    if not download_param:
        log.error("CDN 上传失败: %s | curl stdout: %s", headers_text.strip(), result.stdout[:200] if result else "")
        return False

    # 4. 发送文件消息
    aeskey_b64 = base64.b64encode(aeskey_hex.encode()).decode()
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"claude-wechat-{int(time.time())}-{uuid.uuid4().hex[:8]}",
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [{
                "type": 4,
                "file_item": {
                    "media": {
                        "encrypt_query_param": download_param,
                        "aes_key": aeskey_b64,
                        "encrypt_type": 1,
                    },
                    "file_name": file_path.name,
                    "len": str(rawsize),
                },
            }],
        },
        "base_info": {"channel_version": "1.0.2"},
    }

    try:
        ilink_post(token, "sendmessage", body)
        log.info("文件已发送: %s (%d bytes)", file_path.name, rawsize)
        return True
    except Exception as e:
        log.error("发送文件消息失败: %s", e)
        return False


def send_typing(token, context_token, to_user_id):
    """发送'正在输入'状态"""
    try:
        config = ilink_post(token, "getconfig", {})
        ticket = config.get("typing_ticket", "")
        if ticket:
            ilink_post(token, "sendtyping", {
                "context_token": context_token,
                "to_user_id": to_user_id,
                "typing_ticket": ticket,
            })
    except Exception:
        pass


# ─── 消息处理 ────────────────────────────────────────────

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma", ".opus", ".silk"}

# 对话上下文管理
chat_session = ChatSession("wechat")


def save_last_contact(context_token, user_id):
    """保存最近一次联系人信息，供外部脚本发文件用"""
    LAST_CONTACT_PATH.write_text(json.dumps({
        "context_token": context_token,
        "user_id": user_id,
        "timestamp": time.time(),
    }))


def process_send_queue(token, context_token, to_user_id):
    """检查并处理待发送文件队列"""
    if not SEND_QUEUE_PATH.exists():
        return
    try:
        queue = json.loads(SEND_QUEUE_PATH.read_text())
        files = queue.get("files", [])
        if not files:
            SEND_QUEUE_PATH.unlink(missing_ok=True)
            return
        for fpath in files:
            fp = Path(fpath)
            if fp.exists():
                log.info("从队列发送文件: %s", fp.name)
                ok = send_file(token, context_token, to_user_id, fp)
                if ok:
                    send_text(token, context_token, to_user_id, f"📎 已发送文件：{fp.name}")
            else:
                log.warning("队列中的文件不存在: %s", fpath)
                send_text(token, context_token, to_user_id, f"⚠️ 文件不存在：{fp.name}")
        SEND_QUEUE_PATH.unlink(missing_ok=True)
    except Exception as e:
        log.error("处理发送队列出错: %s", e)
        SEND_QUEUE_PATH.unlink(missing_ok=True)


def send_new_output_files(token, context_token, to_user_id, before_time):
    """发送 output/ 中 before_time 之后生成的新文件"""
    if not OUTPUT_DIR.exists():
        return
    for f in OUTPUT_DIR.iterdir():
        if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime > before_time:
            try:
                send_file(token, context_token, to_user_id, f)
            except Exception as e:
                log.error("发送输出文件失败 %s: %s", f.name, e)


SEND_FILE_HINT = (
    '\n\n【发文件方法】如果用户要求你发送文件到微信，请将文件的绝对路径写入队列文件：\n'
    f'echo \'{{"files": ["/绝对/路径/文件名"]}}\' > {SEND_QUEUE_PATH}\n'
    '支持多个文件：{"files": ["/路径1", "/路径2"]}\n'
    'Bot 会自动读取队列并通过微信发送。写完队列后告诉用户"文件已加入发送队列"即可。'
)


def handle_text(token, msg, content):
    """处理文本消息"""
    context_token = msg["context_token"]
    from_user = msg["from_user_id"]

    # 保存联系信息 & 处理待发送队列
    save_last_contact(context_token, from_user)
    process_send_queue(token, context_token, from_user)

    send_typing(token, context_token, from_user)
    log.info("收到文本: %s", content[:100])

    before_time = time.time()

    try:
        response = chat_session.run_claude(content, extra_prompt=SEND_FILE_HINT)
        send_text(token, context_token, from_user, response)
        # Claude 执行完后检查队列和新输出文件
        process_send_queue(token, context_token, from_user)
        send_new_output_files(token, context_token, from_user, before_time)
    except Exception as e:
        log.error("处理文本出错: %s", e)
        send_text(token, context_token, from_user, f"❌ 出错了：{e}")


def handle_media(token, msg, item):
    """处理媒体消息（图片/语音/文件/视频）"""
    context_token = msg["context_token"]
    from_user = msg["from_user_id"]
    msg_type = item.get("type", 0)

    save_last_contact(context_token, from_user)
    process_send_queue(token, context_token, from_user)
    send_typing(token, context_token, from_user)

    # 根据类型从不同子字段提取媒体信息
    # type 2: image_item, type 3: voice_item, type 4: file_item, type 5: video_item
    type_key_map = {2: "image_item", 3: "voice_item", 4: "file_item", 5: "video_item"}
    media_key = type_key_map.get(msg_type, "")
    media_item = item.get(media_key, {})

    # 媒体信息在 media 子对象里
    media = media_item.get("media", {})
    encrypt_query_param = media.get("encrypt_query_param", "")
    aes_key = media.get("aes_key", "")

    # 文件名
    filename = media_item.get("file_name", "")

    type_map = {2: "image", 3: "voice", 4: "file", 5: "video"}
    type_name = type_map.get(msg_type, "unknown")

    if not filename:
        ext_map = {2: ".jpg", 3: ".silk", 5: ".mp4"}
        ext = ext_map.get(msg_type, "")
        filename = f"{type_name}_{int(time.time())}{ext}"

    ext = Path(filename).suffix.lower()
    if ext in AUDIO_EXTENSIONS or msg_type in (3, 5):
        save_dir = INBOX_DIR / "audio"
    else:
        save_dir = INBOX_DIR / "files"

    save_path = save_dir / filename

    if not encrypt_query_param:
        log.error("媒体消息缺少 encrypt_query_param: %s", item)
        send_text(token, context_token, from_user, "⚠️ 无法下载：消息中缺少媒体数据")
        return

    try:
        download_media(encrypt_query_param, aes_key, save_path)
    except Exception as e:
        log.error("下载媒体失败: %s", e)
        send_text(token, context_token, from_user, f"⚠️ 下载文件失败：{e}")
        return

    rel_path = save_path.relative_to(KNOWLEDGE_DIR)
    file_size = save_path.stat().st_size

    if msg_type == 2:
        prompt = (
            f"用户发来了一张图片，已保存到 {rel_path}。"
            f"请查看图片内容并告诉用户你看到了什么。如果包含有用信息，请录入知识库。"
        )
    elif msg_type == 3:
        prompt = (
            f"用户发来了一条语音消息，已保存到 {rel_path}（{file_size} 字节）。"
            f"请使用 transcribe_audio 工具转写后告诉用户内容。"
        )
    elif msg_type == 4:
        prompt = (
            f"用户发来了一个文件 {filename}，已保存到 {rel_path}（{file_size} 字节）。"
            f"请分析文件内容并告诉用户关键信息。如果适合录入知识库，请录入。"
        )
    elif msg_type == 5:
        prompt = (
            f"用户发来了一个视频文件，已保存到 {rel_path}（{file_size} 字节）。"
            f"文件已保存，请告知用户。"
        )
    else:
        prompt = f"用户发来了未知类型({msg_type})的消息，已保存到 {rel_path}。"

    try:
        response = chat_session.run_claude(prompt)
        send_text(token, context_token, from_user, response)
        process_send_queue(token, context_token, from_user)
    except Exception as e:
        log.error("处理媒体出错: %s", e)
        send_text(token, context_token, from_user, f"❌ 出错了：{e}")


# ─── 主循环 ──────────────────────────────────────────────

def poll_loop(token):
    """长轮询主循环"""
    cursor = load_cursor()
    retry_delay = 1

    log.info("开始监听消息…")

    while True:
        try:
            body = {
                "base_info": {"channel_version": "1.0.2"},
                "get_updates_buf": cursor,
            }
            data = ilink_post(token, "getupdates", body, timeout=40)

            new_cursor = data.get("get_updates_buf", cursor)
            if new_cursor != cursor:
                cursor = new_cursor
                save_cursor(cursor)

            for update in data.get("msgs", []):
                context_token = update.get("context_token", "")
                from_user = update.get("from_user_id", "")
                msg = {"context_token": context_token, "from_user_id": from_user}

                for item in update.get("item_list", []):
                    msg_type = item.get("type", 0)

                    if msg_type == 1:
                        # 文本在 text_item.text 里
                        text = item.get("text_item", {}).get("text", "")
                        if text:
                            handle_text(token, msg, text)
                    elif msg_type in (2, 3, 4, 5):
                        handle_media(token, msg, item)
                    else:
                        log.info("忽略未知消息类型: %d", msg_type)

            retry_delay = 1

        except subprocess.TimeoutExpired:
            continue
        except json.JSONDecodeError as e:
            log.warning("JSON 解析失败（可能是空响应/超时）: %s", e)
            continue
        except KeyboardInterrupt:
            log.info("用户中断，退出")
            return "quit"
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str and "expired" in err_str:
                log.error("Session 已过期，需要重新扫码登录")
                return "expired"
            log.error("轮询出错: %s", e)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


# ─── 主入口 ──────────────────────────────────────────────

def main():
    print("🤖 微信知识库 Bot 启动中…")

    token = load_env()

    if not token:
        log.info("未找到已保存的 token，需要扫码登录")
        token = login()
    else:
        log.info("使用已保存的 token")

    while True:
        result = poll_loop(token)
        if result == "expired":
            print("\n⚠️ Token 已过期，重新登录…\n")
            token = login()
        elif result == "quit":
            break
        else:
            break

    print("Bot 已停止")


if __name__ == "__main__":
    main()
