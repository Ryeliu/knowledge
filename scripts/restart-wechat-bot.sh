#!/bin/bash
# 重启微信 Bot（通过 launchctl，launchd 会自动拉起新进程）
SERVICE="com.wechat.bot"

echo "重启 $SERVICE ..."
launchctl kickstart -k "gui/$(id -u)/$SERVICE"
sleep 2

PID=$(launchctl list | grep "$SERVICE" | awk '{print $1}')
echo "微信 Bot 已重启 (PID $PID)"
