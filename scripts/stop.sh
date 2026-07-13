#!/bin/bash
# 停止脚本

echo "🛑 停止小鶴神..."

# 查找并杀掉进程
pkill -f "python.*bot.py"
echo "✅ 已停止"
