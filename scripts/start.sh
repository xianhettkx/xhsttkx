#!/bin/bash
# 启动脚本

echo "🦅 小鶴神 · 启动中..."

cd "$(dirname "$0")/.."

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi

# 安装依赖
echo "📦 安装依赖..."
pip install -r requirements.txt

# 创建目录
mkdir -p data/user_data data/sessions logs

# 启动
echo "🚀 启动机器人..."
python3 bot.py
