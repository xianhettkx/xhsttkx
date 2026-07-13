#!/bin/bash
# 监控脚本

echo "📊 小鶴神 · 监控面板"
echo "====================="
echo ""

# 检查进程
if pgrep -f "python.*bot.py" > /dev/null; then
    echo "✅ 状态: 运行中"
    echo ""
    echo "📋 最近日志:"
    tail -20 logs/bot.log 2>/dev/null || echo "无日志"
else
    echo "❌ 状态: 已停止"
fi

echo ""
echo "💾 磁盘使用:"
df -h | grep -E "(Filesystem|/dev)"
