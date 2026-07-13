# 小鶴神 · PC28 自动投注机器人 🤖

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://telegram.org)

加拿大28 智能预测自动下注机器人，支持多种算法和倍投策略。

## ✨ 功能特点

- 🎯 **多算法预测**: CIT杀组、悲天悯人5Y/7Y/混合、统计规律、全部融合
- 📊 **算法胜率排行榜**: 实时统计每个算法的准确率
- ⚡ **13/14倍投**: 遇到13/14自动触发倍投
- 💰 **余额追踪**: 通过 @kkpay 自动查询余额
- 📈 **盈亏统计**: 每日盈亏自动记录
- 🔄 **自动重连**: 断线自动重连，7x24小时运行
- 📱 **简单易用**: Telegram 按钮操作

## 📦 部署

### 方式一：Docker 部署

```bash
# 克隆项目
git clone https://github.com/yourusername/pc28-bot.git
cd pc28-bot

# 配置环境变量
cp .env.example .env
nano .env

# 启动
docker-compose up -d
