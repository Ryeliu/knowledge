# 简易文件上传服务 — 开发计划

> **状态：待开发**

---

## 目标

搭建一个轻量级的本地文件上传服务，解决 Telegram Bot API 20MB 下载限制的问题。手机浏览器访问上传页面，选择文件直接传到 inbox/，无大小限制。

---

## 架构

```
手机浏览器 → https://你的Mac:8443/upload → 文件保存到 inbox/
                                          ↓
                                    Bot 通知你文件已到达
                                          ↓
                                    你在 Telegram 发指令处理
```

---

## 技术方案

| 组件 | 方案 | 说明 |
|------|------|------|
| Web 框架 | Python Flask 或 http.server | 极简，不引入额外依赖 |
| 上传页面 | 单个 HTML 页面 | 支持拖拽上传、进度条 |
| HTTPS | 自签名证书 | 手机浏览器访问需要 HTTPS |
| 外网访问 | 内网穿透（可选） | 只在同一 Wi-Fi 下用则不需要 |
| 通知 | Telegram Bot 主动推送 | 文件到达后通知你 |

---

## 功能设计

### 上传页面（单页 HTML）

- 拖拽或点击选择文件
- 显示上传进度条
- 支持多文件批量上传
- 上传完成后显示文件名和大小
- 可选：附带一行文字备注（相当于 caption）

### 上传服务（Python 脚本）

- 监听端口（如 8443）
- POST /upload → 保存文件到 inbox/
- 文件保存后通过 Telegram Bot API 主动通知你：「📥 收到文件：xxx.m4a（128MB）」
- 可选：自动触发 Claude Code 处理

### 安全措施

- 仅监听局域网 IP（不暴露到公网），或通过 token 鉴权
- 上传接口需要携带预设 token（URL 参数或 header）
- 可选：限制上传来源 IP

---

## 新增文件

```
~/worklab/Sidejob/knowledge/
├── scripts/
│   ├── upload_server.py    # 上传服务主脚本
│   └── upload.html         # 上传页面
```

---

## 访问方式

### 同一 Wi-Fi 下（最简单）

1. Mac 上启动 upload_server.py
2. 手机浏览器访问 `https://192.168.x.x:8443/upload?token=xxx`
3. 选择文件上传

### 外网访问（可选，需内网穿透）

- 方案 A：使用 Cloudflare Tunnel（免费，`cloudflared tunnel`）
- 方案 B：使用 ngrok / frp
- 方案 C：Tailscale（组网后直接访问，无需穿透）

---

## launchd 自启（可选）

和 bot 一样，可以配置 LaunchAgent 开机自启：

```
~/Library/LaunchAgents/com.knowledge.upload.plist
```

---

## 与现有系统的集成

1. 文件保存到 `inbox/` 后，Bot 主动推送通知到 Telegram
2. 你在 Telegram 回复指令，如 `转写 inbox/meeting.m4a`
3. Claude Code 处理并返回结果

完整流程示例：
```
手机上传 2小时会议录音 meeting.m4a（150MB）
  → Bot 通知：📥 收到文件 meeting.m4a（150MB）
  → 你回复：转写这个会议录音
  → whisper + pyannote 处理（依赖声纹转写功能开发完成）
  → 结果录入知识库
```

---

## 依赖

- Flask（`pip3 install flask`）或使用 Python 标准库 http.server
- 自签名证书生成：`openssl req -x509 -newkey rsa:2048 ...`

---

## 待确认

- [ ] 是否需要外网访问能力（还是仅限同一 Wi-Fi）
- [ ] 是否需要上传密码/token 保护
- [ ] 是否与声纹转写功能一起开发（上传音频后自动转写）
- [ ] 上传文件大小上限设置（建议 500MB 或 1GB）
