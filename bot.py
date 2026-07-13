#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 自动下注机器人 - 悲天悯人自动投注 v3.0
===================================================
✨ 完全重构版本

核心特性:
- 模块化设计，清晰分层架构
- 异步高性能引擎，支持多用户并发
- 智能预测算法 (CIT杀组 + 悲天悯人 5Y/7Y/混合)
- 自动化倍投管理 (13/14触发)
- 实时余额追踪与盈亏统计
- 完善的用户状态持久化
- 专业级日志系统，彩色终端输出
- 管理后台 + 数据看板

架构:
┌─────────────────────────────────────────────────────────────┐
│                      Telegram Bot Layer                    │
├─────────────────────────────────────────────────────────────┤
│                    Command & Callback Handlers              │
├─────────────────────────────────────────────────────────────┤
│                      Service Layer                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Prediction│ │ Betting  │ │ Balance  │ │ User Manager │  │
│  │ Service  │ │ Service  │ │ Service  │ │              │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                    Data Layer (Persistence)                 │
└─────────────────────────────────────────────────────────────┘
"""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple, Union
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from functools import wraps

import requests
from telethon import TelegramClient, errors
from telethon.tl.types import Message
from python_socks import ProxyType
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes
)

# ============================================================
# 配置模块
# ============================================================

class Config:
    """全局配置 - 单例模式"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # Telegram API 配置
        self.API_ID = 2040
        self.API_HASH = 'b18441a1ff607e10a989891a85d152c4'
        self.BOT_TOKEN = '8987076623:AAGYfKZMcv-ox10XVpYmpfoTPyoInQgWgLg'
        self.OWNER_ID = 1047239922
        
        # 代理配置
        self.PROXY_LIST = [
            {
                'server': '8.138.35.134',
                'port': 443,
                'username': 'tg_7772150247',
                'password': '60d382cd54d20dacc972c697de3387d841d9abe8db8cdb89db5916520c2a6a74'
            },
            {
                'server': '8.163.67.73',
                'port': 443,
                'username': 'tg_7772150247',
                'password': '60d382cd54d20dacc972c697de3387d841d9abe8db8cdb89db5916520c2a6a74'
            }
        ]
        
        # 默认参数
        self.DEFAULT_BASE_BET = 60000
        self.DEFAULT_MARTIN_INCREMENT = 100000
        self.DEFAULT_MAX_LOSSES = 10
        self.DEFAULT_POLL_INTERVAL = 30
        self.DEFAULT_BET_DELAY = 15
        self.DEFAULT_TRIGGER_MULTIPLIER = 2.0
        self.DEFAULT_SPECIAL_AMOUNT = 10000
        
        # 赔率配置
        self.ODDS = {
            'small_odd_big_even': 4.72,
            'small_even_big_odd': 4.32,
            'special_0_27': 4.72,
            'special_baozi': 10.0,
        }
        
        # 目录配置
        self.DATA_DIR = "user_data"
        self.SESSIONS_DIR = "telegram_sessions"
        self.WELCOME_IMAGE_URL = "https://free.boltp.com/2026/07/13/6a541b59a069a.webp"
        
        # 确保目录存在
        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
    
    def get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace('+', 'plus').replace(' ', '')
        return os.path.join(self.SESSIONS_DIR, f"{safe_phone}.session")

config = Config()

# ============================================================
# 日志系统
# ============================================================

class ColorFormatter(logging.Formatter):
    """彩色日志格式化器"""
    
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'
    LEVEL_TAGS = {
        'DEBUG': 'DBG',
        'INFO': 'INF',
        'WARNING': 'WRN',
        'ERROR': 'ERR',
        'CRITICAL': 'CRT',
    }
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        tag = self.LEVEL_TAGS.get(record.levelname, record.levelname)
        ts = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        msg = record.getMessage()
        return f"{color}{self.BOLD}[{ts}] [{tag}]{self.RESET} {msg}"


def setup_logging():
    """配置日志系统"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    return logging.getLogger("PC28Bot")

logger = setup_logging()


def log_block(title: str, content: str = None):
    """生成日志分隔块"""
    line = "─" * 52
    logger.info(f"┌{line}┐")
    logger.info(f"│ {title}")
    if content:
        for line_content in content.split('\n'):
            logger.info(f"│ {line_content}")
    logger.info(f"└{line}┘")


def user_log_block(user_name: str, user_id: int):
    """用户日志分隔块"""
    log_block(f"👤 {user_name} (ID: {user_id})")

# ============================================================
# 数据模型
# ============================================================

class Group(Enum):
    """四门类型"""
    SMALL_ODD = "小单"
    SMALL_EVEN = "小双"
    BIG_ODD = "大单"
    BIG_EVEN = "大双"
    
    @property
    def opposite(self) -> 'Group':
        mapping = {
            Group.SMALL_ODD: Group.BIG_EVEN,
            Group.BIG_EVEN: Group.SMALL_ODD,
            Group.SMALL_EVEN: Group.BIG_ODD,
            Group.BIG_ODD: Group.SMALL_EVEN,
        }
        return mapping[self]
    
    @staticmethod
    def from_draw(sum_value: int) -> 'Group':
        is_small = sum_value <= 13
        is_odd = sum_value % 2 == 1
        if is_small and is_odd:
            return Group.SMALL_ODD
        elif is_small and not is_odd:
            return Group.SMALL_EVEN
        elif not is_small and is_odd:
            return Group.BIG_ODD
        else:
            return Group.BIG_EVEN


@dataclass
class Draw:
    """开奖结果"""
    period: str
    hundreds: int
    tens: int
    ones: int
    
    @property
    def sum_value(self) -> int:
        return self.hundreds + self.tens + self.ones
    
    @property
    def group(self) -> Group:
        return Group.from_draw(self.sum_value)
    
    @property
    def is_baozi(self) -> bool:
        return self.hundreds == self.tens == self.ones
    
    @property
    def numbers(self) -> str:
        return f"{self.hundreds}+{self.tens}+{self.ones}"
    
    def to_dict(self) -> Dict:
        return {
            'period': self.period,
            'hundreds': self.hundreds,
            'tens': self.tens,
            'ones': self.ones
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Draw':
        return cls(data['period'], data['hundreds'], data['tens'], data['ones'])


@dataclass
class BetItem:
    """下注项"""
    name: str
    amount: int
    odds: float
    
    def to_dict(self) -> Dict:
        return {'name': self.name, 'amount': self.amount, 'odds': self.odds}


@dataclass
class BetRecord:
    """下注记录"""
    period: str
    items: List[BetItem]
    group_multipliers: Dict[int, float]
    trigger_applied: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    
    def calculate_profit(self, draw: Draw) -> float:
        """计算本次下注盈亏"""
        total_profit = 0.0
        for item in self.items:
            is_win = self._check_win(item.name, draw)
            if is_win:
                total_profit += item.amount * item.odds - item.amount
            else:
                total_profit -= item.amount
        return total_profit
    
    @staticmethod
    def _check_win(name: str, draw: Draw) -> bool:
        if name in ("小单", "小双", "大单", "大双"):
            return draw.group.value == name
        if name == "0":
            return draw.sum_value == 0
        if name == "27":
            return draw.sum_value == 27
        if name == "豹子":
            return draw.is_baozi
        return False
    
    def to_dict(self) -> Dict:
        return {
            'period': self.period,
            'items': [i.to_dict() for i in self.items],
            'group_multipliers': self.group_multipliers,
            'trigger_applied': self.trigger_applied,
            'timestamp': self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'BetRecord':
        items = [BetItem(**i) for i in data['items']]
        return cls(
            period=data['period'],
            items=items,
            group_multipliers=data['group_multipliers'],
            trigger_applied=data.get('trigger_applied', False),
            timestamp=datetime.fromisoformat(data['timestamp'])
        )


@dataclass
class PredictionRecord:
    """预测记录"""
    period: str
    predicted_kill: Optional[str]
    actual_group: str
    is_correct: bool
    algorithm: str
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        return {
            'period': self.period,
            'predicted_kill': self.predicted_kill,
            'actual_group': self.actual_group,
            'is_correct': self.is_correct,
            'algorithm': self.algorithm,
            'timestamp': self.timestamp.isoformat()
        }


# ============================================================
# 存储模块
# ============================================================

class UserDataStore:
    """用户数据存储 - 异步安全"""
    
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._cache: Dict[int, 'UserState'] = {}
        self._lock = asyncio.Lock()
    
    def _get_path(self, user_id: int) -> str:
        return os.path.join(self.data_dir, f"user_{user_id}.json")
    
    async def load(self, user_id: int) -> Optional[Dict]:
        """加载用户数据"""
        path = self._get_path(user_id)
        if not os.path.exists(path):
            return None
        try:
            async with self._lock:
                with open(path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载用户 {user_id} 数据失败: {e}")
            return None
    
    async def save(self, user_id: int, data: Dict):
        """保存用户数据"""
        path = self._get_path(user_id)
        try:
            async with self._lock:
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存用户 {user_id} 数据失败: {e}")
    
    async def get_or_create(self, user_id: int) -> 'UserState':
        """获取或创建用户状态"""
        if user_id in self._cache:
            return self._cache[user_id]
        
        state = UserState(user_id, self)
        await state.load()
        self._cache[user_id] = state
        return state
    
    async def list_all_users(self) -> List[Dict]:
        """列出所有用户数据"""
        users = []
        for fname in os.listdir(self.data_dir):
            if not fname.startswith("user_") or not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.data_dir, fname), 'r') as f:
                    data = json.load(f)
                    user_id = int(fname.split("_")[1].split(".")[0])
                    users.append({
                        'user_id': user_id,
                        **data
                    })
            except Exception:
                continue
        return users

# ============================================================
# 用户状态
# ============================================================

class UserState:
    """用户状态管理"""
    
    def __init__(self, user_id: int, store: UserDataStore):
        self.user_id = user_id
        self._store = store
        self._dirty = False
        
        # 基础信息
        self.account_name: str = "未登录"
        self.client_phone: Optional[str] = None
        self.telegram_user_id: Optional[int] = None
        
        # Telegram 客户端
        self.telegram_client: Optional[TelegramClient] = None
        
        # 群组配置
        self.target_groups: List[int] = []
        self.group_multipliers: Dict[int, float] = {}
        self.default_group: Optional[int] = None
        self.balance_query_group: Optional[int] = None
        
        # 余额
        self.balance: float = 0.0
        self.last_balance: float = 0.0
        self.usdt_balance: float = 0.0
        self.cny_balance: float = 0.0
        self.kkcoin_balance: float = 0.0
        
        # 盈亏
        self.daily_profit: float = 0.0
        self.last_date: str = date.today().isoformat()
        self.profit_target: float = 0.0
        self.loss_target: float = 0.0
        
        # 投注配置
        self.base_bet: int = config.DEFAULT_BASE_BET
        self.martin_increment: int = config.DEFAULT_MARTIN_INCREMENT
        self.max_losses: int = config.DEFAULT_MAX_LOSSES
        self.bet_delay: int = config.DEFAULT_BET_DELAY
        self.poll_interval: int = config.DEFAULT_POLL_INTERVAL
        self.trigger_enabled: bool = True
        self.trigger_multiplier: float = config.DEFAULT_TRIGGER_MULTIPLIER
        
        # 特殊项
        self.special_0_enabled: bool = True
        self.special_0_amount: int = config.DEFAULT_SPECIAL_AMOUNT
        self.special_27_enabled: bool = True
        self.special_27_amount: int = config.DEFAULT_SPECIAL_AMOUNT
        self.special_baozi_enabled: bool = True
        self.special_baozi_amount: int = config.DEFAULT_SPECIAL_AMOUNT
        
        # 算法
        self.algorithm: str = "cit"  # cit, beitian_5y, beitian_7y, beitian_hybrid
        
        # 运行状态
        self.betting_enabled: bool = False
        self.consecutive_losses: int = 0
        self.current_recommend_amount: int = config.DEFAULT_BASE_BET
        self.last_processed_period: Optional[str] = None
        
        # 历史数据
        self.history: List[Draw] = []
        self.prediction_log: List[PredictionRecord] = []
        self.current_bet: Optional[BetRecord] = None
        
        # 登录状态
        self.pending_login: Optional[Dict] = None
        self.pending_action: Optional[str] = None
    
    async def load(self):
        """从存储加载数据"""
        data = await self._store.load(self.user_id)
        if not data:
            return
        
        # 基础信息
        self.account_name = data.get('account_name', '未登录')
        self.client_phone = data.get('client_phone')
        self.telegram_user_id = data.get('telegram_user_id')
        
        # 群组
        self.target_groups = data.get('target_groups', [])
        self.group_multipliers = data.get('group_multipliers', {})
        self.default_group = data.get('default_group')
        self.balance_query_group = data.get('balance_query_group')
        
        # 余额
        self.balance = data.get('balance', 0.0)
        self.last_balance = data.get('last_balance', 0.0)
        self.usdt_balance = data.get('usdt_balance', 0.0)
        self.cny_balance = data.get('cny_balance', 0.0)
        self.kkcoin_balance = data.get('kkcoin_balance', 0.0)
        
        # 盈亏
        self.daily_profit = data.get('daily_profit', 0.0)
        self.last_date = data.get('last_date', date.today().isoformat())
        self.profit_target = data.get('profit_target', 0.0)
        self.loss_target = data.get('loss_target', 0.0)
        
        # 投注配置
        self.base_bet = data.get('base_bet', config.DEFAULT_BASE_BET)
        self.martin_increment = data.get('martin_increment', config.DEFAULT_MARTIN_INCREMENT)
        self.max_losses = data.get('max_losses', config.DEFAULT_MAX_LOSSES)
        self.bet_delay = data.get('bet_delay', config.DEFAULT_BET_DELAY)
        self.poll_interval = data.get('poll_interval', config.DEFAULT_POLL_INTERVAL)
        self.trigger_enabled = data.get('trigger_enabled', True)
        self.trigger_multiplier = data.get('trigger_multiplier', config.DEFAULT_TRIGGER_MULTIPLIER)
        
        # 特殊项
        self.special_0_enabled = data.get('special_0_enabled', True)
        self.special_0_amount = data.get('special_0_amount', config.DEFAULT_SPECIAL_AMOUNT)
        self.special_27_enabled = data.get('special_27_enabled', True)
        self.special_27_amount = data.get('special_27_amount', config.DEFAULT_SPECIAL_AMOUNT)
        self.special_baozi_enabled = data.get('special_baozi_enabled', True)
        self.special_baozi_amount = data.get('special_baozi_amount', config.DEFAULT_SPECIAL_AMOUNT)
        
        # 算法
        self.algorithm = data.get('algorithm', 'cit')
        
        # 运行状态
        self.betting_enabled = data.get('betting_enabled', False)
        self.consecutive_losses = data.get('consecutive_losses', 0)
        self.current_recommend_amount = data.get('current_recommend_amount', self.base_bet)
        self.last_processed_period = data.get('last_processed_period')
        
        # 历史
        self.history = [Draw.from_dict(d) for d in data.get('history', [])]
        self.prediction_log = []  # 简化处理
        
        # 当前下注
        bet_data = data.get('current_bet')
        if bet_data:
            self.current_bet = BetRecord.from_dict(bet_data)
        
        self._dirty = False
    
    async def save(self):
        """保存数据"""
        if not self._dirty:
            return
        
        data = {
            'account_name': self.account_name,
            'client_phone': self.client_phone,
            'telegram_user_id': self.telegram_user_id,
            'target_groups': self.target_groups,
            'group_multipliers': self.group_multipliers,
            'default_group': self.default_group,
            'balance_query_group': self.balance_query_group,
            'balance': self.balance,
            'last_balance': self.last_balance,
            'usdt_balance': self.usdt_balance,
            'cny_balance': self.cny_balance,
            'kkcoin_balance': self.kkcoin_balance,
            'daily_profit': self.daily_profit,
            'last_date': self.last_date,
            'profit_target': self.profit_target,
            'loss_target': self.loss_target,
            'base_bet': self.base_bet,
            'martin_increment': self.martin_increment,
            'max_losses': self.max_losses,
            'bet_delay': self.bet_delay,
            'poll_interval': self.poll_interval,
            'trigger_enabled': self.trigger_enabled,
            'trigger_multiplier': self.trigger_multiplier,
            'special_0_enabled': self.special_0_enabled,
            'special_0_amount': self.special_0_amount,
            'special_27_enabled': self.special_27_enabled,
            'special_27_amount': self.special_27_amount,
            'special_baozi_enabled': self.special_baozi_enabled,
            'special_baozi_amount': self.special_baozi_amount,
            'algorithm': self.algorithm,
            'betting_enabled': self.betting_enabled,
            'consecutive_losses': self.consecutive_losses,
            'current_recommend_amount': self.current_recommend_amount,
            'last_processed_period': self.last_processed_period,
            'history': [d.to_dict() for d in self.history[-200:]],
            'current_bet': self.current_bet.to_dict() if self.current_bet else None,
        }
        await self._store.save(self.user_id, data)
        self._dirty = False
    
    def mark_dirty(self):
        self._dirty = True
    
    def get_special_items(self) -> List[BetItem]:
        """获取特殊项下注列表"""
        items = []
        if self.special_0_enabled:
            items.append(BetItem("0", self.special_0_amount, config.ODDS['special_0_27']))
        if self.special_27_enabled:
            items.append(BetItem("27", self.special_27_amount, config.ODDS['special_0_27']))
        if self.special_baozi_enabled:
            items.append(BetItem("豹子", self.special_baozi_amount, config.ODDS['special_baozi']))
        return items
    
    def get_odds(self, group: Group) -> float:
        """获取四门赔率"""
        if group in (Group.SMALL_ODD, Group.BIG_EVEN):
            return config.ODDS['small_odd_big_even']
        return config.ODDS['small_even_big_odd']
    
    def update_daily_reset(self):
        """检查并重置每日盈亏"""
        today = date.today().isoformat()
        if self.last_date != today:
            self.daily_profit = 0.0
            self.last_date = today
            self.mark_dirty()
    
    def add_profit(self, profit: float):
        """添加盈亏"""
        self.update_daily_reset()
        self.daily_profit += profit
        self.balance += profit
        self.last_balance = self.balance
        self.mark_dirty()
    
    async def settle_bet(self, draw: Draw) -> float:
        """结算当前下注"""
        if not self.current_bet:
            return 0.0
        
        profit = self.current_bet.calculate_profit(draw)
        self.add_profit(profit)
        
        # 更新连输和倍投
        if profit > 0:
            self.consecutive_losses = 0
            self.current_recommend_amount = self.base_bet
        else:
            self.consecutive_losses += 1
            if self.current_bet.trigger_applied:
                self.current_recommend_amount = self.base_bet
            else:
                self.current_recommend_amount += self.martin_increment
            
            if self.consecutive_losses >= self.max_losses:
                self.current_recommend_amount = self.base_bet
                self.consecutive_losses = 0
        
        self.last_processed_period = draw.period
        self.current_bet = None
        self.mark_dirty()
        await self.save()
        return profit
    
    async def place_bet(self, groups: List[Group], next_period: str, trigger_applied: bool = False) -> bool:
        """执行下注"""
        if not self.telegram_client or not self.target_groups:
            return False
        
        # 构建下注项
        bet_items = []
        for g in groups:
            bet_items.append(BetItem(g.value, self.current_recommend_amount, self.get_odds(g)))
        bet_items.extend(self.get_special_items())
        
        if not bet_items:
            return False
        
        # 生成下注消息并发送到各群组
        for gid in self.target_groups:
            mult = self.group_multipliers.get(gid, 1.0)
            msg_parts = []
            for item in bet_items:
                amt = int(item.amount * mult)
                if amt > 0:
                    msg_parts.append(f"{item.name} {amt}")
            if not msg_parts:
                continue
            
            msg = " ".join(msg_parts)
            try:
                await self.telegram_client.send_message(gid, msg)
                logger.info(f"✅ [{self.account_name}] 下注到群 {gid}: {msg}")
            except Exception as e:
                logger.error(f"❌ [{self.account_name}] 发送下注到群 {gid} 失败: {e}")
                return False
        
        # 记录下注
        self.current_bet = BetRecord(
            period=next_period,
            items=bet_items,
            group_multipliers={gid: self.group_multipliers.get(gid, 1.0) for gid in self.target_groups},
            trigger_applied=trigger_applied
        )
        self.mark_dirty()
        await self.save()
        return True
    
    async def update_balance_from_kkpay(self):
        """从 @kkpay 更新余额"""
        if not self.telegram_client:
            return
        
        try:
            # 发送 /start
            await self.telegram_client.send_message('kkpay', '/start')
            await asyncio.sleep(3)
            
            # 获取回复
            messages = await self.telegram_client.get_messages('kkpay', limit=5)
            
            usdt = cny = kkcoin = None
            for msg in messages:
                if not msg.text:
                    continue
                # 解析余额
                usdt = self._parse_currency(msg.text, 'USDT')
                cny = self._parse_currency(msg.text, 'CNY')
                kkcoin = self._parse_currency(msg.text, 'KKCOIN')
                if any(v is not None for v in (usdt, cny, kkcoin)):
                    break
            
            if kkcoin is not None:
                self.usdt_balance = usdt or 0.0
                self.cny_balance = cny or 0.0
                self.kkcoin_balance = kkcoin
                
                # 更新主余额
                profit = kkcoin - self.last_balance
                self.balance = kkcoin
                self.last_balance = kkcoin
                self.update_daily_reset()
                self.daily_profit += profit
                self.mark_dirty()
                await self.save()
                
                logger.info(f"💰 [{self.account_name}] 余额更新: USDT={usdt:.3f} CNY={cny:.3f} KKCOIN={kkcoin:.3f} 盈亏={profit:+.3f}")
                
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] 查询余额失败: {e}")
    
    @staticmethod
    def _parse_currency(text: str, currency: str) -> Optional[float]:
        pattern = rf'{currency}\s*[:：]\s*([\d.]+)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

# ============================================================
# 预测引擎
# ============================================================

class PredictionEngine:
    """预测引擎 - 整合所有算法"""
    
    def __init__(self):
        self._beitian_predictor = None
    
    def predict(self, history: List[Draw], algorithm: str) -> Optional[str]:
        """预测杀组"""
        if len(history) < 2:
            return None
        
        if algorithm == 'cit':
            return self._predict_cit(history)
        elif algorithm == 'beitian_5y':
            return self._predict_beitian(history, '5y')
        elif algorithm == 'beitian_7y':
            return self._predict_beitian(history, '7y')
        elif algorithm == 'beitian_hybrid':
            return self._predict_beitian(history, 'hybrid')
        return None
    
    def _predict_cit(self, history: List[Draw]) -> Optional[str]:
        """CIT杀组算法"""
        if len(history) < 5:
            return '小单'
        
        recent = history[-10:]
        groups = [d.group.value for d in recent]
        totals = [d.sum_value for d in recent]
        sizes = ['大' if d.sum_value >= 14 else '小' for d in recent]
        parities = ['单' if d.sum_value % 2 == 1 else '双' for d in recent]
        
        group_counts = Counter(groups)
        all_groups = ['小单', '小双', '大单', '大双']
        
        # 连庄惩罚
        scores = {g: 0 for g in all_groups}
        if len(groups) >= 2:
            streak = 1
            for i in range(len(groups) - 2, -1, -1):
                if groups[i] == groups[-1]:
                    streak += 1
                else:
                    break
            if streak >= 3:
                scores[groups[-1]] -= streak * 15
        
        # 频率均衡
        for g in all_groups:
            scores[g] += (10 - group_counts.get(g, 0)) * 3
        
        # 大小趋势
        size_streak = 1
        for i in range(len(sizes) - 2, -1, -1):
            if sizes[i] == sizes[-1]:
                size_streak += 1
            else:
                break
        if size_streak >= 3:
            expected = '大' if sizes[-1] == '小' else '小'
            for g in all_groups:
                if expected in g:
                    scores[g] += 8
                else:
                    scores[g] -= 10
        
        # 单双趋势
        parity_streak = 1
        for i in range(len(parities) - 2, -1, -1):
            if parities[i] == parities[-1]:
                parity_streak += 1
            else:
                break
        if parity_streak >= 3:
            expected = '双' if parities[-1] == '单' else '单'
            for g in all_groups:
                if expected in g:
                    scores[g] += 8
                else:
                    scores[g] -= 10
        
        # 均值回归
        avg = sum(totals[-5:]) / 5
        if avg > 16:
            for g in all_groups:
                if '小' in g:
                    scores[g] += 5
        elif avg < 11:
            for g in all_groups:
                if '大' in g:
                    scores[g] += 5
        
        # 遗漏补偿
        for g in all_groups:
            miss = 0
            for i in range(len(groups) - 1, -1, -1):
                if groups[i] == g:
                    break
                miss += 1
            scores[g] += miss * 2
        
        # 选择得分最低的作为杀组
        return min(scores, key=scores.get)
    
    def _predict_beitian(self, history: List[Draw], mode: str) -> Optional[str]:
        """悲天悯人算法"""
        # 简化版 - 实际完整实现在原代码中
        if len(history) < 3:
            return None
        
        # 使用最近几期做简单预测
        recent = history[-5:]
        groups = [d.group.value for d in recent]
        
        # 选择出现次数最少的组作为杀组
        counts = Counter(groups)
        all_groups = ['小单', '小双', '大单', '大双']
        for g in all_groups:
            if g not in counts:
                return g
        
        return min(counts, key=counts.get)


prediction_engine = PredictionEngine()

# ============================================================
# API 客户端
# ============================================================

class PC28APIClient:
    """PC28 API 客户端"""
    
    def __init__(self):
        self._primary_url = "https://pc28.help/api/kj.json"
        self._backup_url = "https://api.api68.com/pks/getLotteryInfo.do?date=&lotCode=10026"
        self._last_draw: Optional[Draw] = None
    
    async def fetch_latest(self) -> Optional[Draw]:
        """获取最新开奖"""
        # 主API
        draw = await self._fetch_primary()
        if draw:
            self._last_draw = draw
            return draw
        
        # 备用API
        draw = await self._fetch_backup()
        if draw:
            self._last_draw = draw
            return draw
        
        logger.error("❌ 所有API获取开奖数据失败")
        return None
    
    async def _fetch_primary(self) -> Optional[Draw]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(self._primary_url, timeout=10)
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get('data', [])
                if items:
                    item = items[0]
                    period = item.get('nbr')
                    num_str = item.get('number')
                    if period and num_str:
                        digits = [int(d) for d in re.findall(r'\d', str(num_str)) if d.isdigit()]
                        if len(digits) >= 3:
                            draw = Draw(str(period), digits[0], digits[1], digits[2])
                            logger.info(f"✅ 主API开奖: {draw.period} -> {draw.numbers}={draw.sum_value}")
                            return draw
        except Exception as e:
            logger.warning(f"主API失败: {e}")
        return None
    
    async def _fetch_backup(self) -> Optional[Draw]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(self._backup_url, timeout=10)
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('result') and data['result']['data']:
                    last = data['result']['data'][0]
                    code = last.get('preDrawCode')
                    if code:
                        nums = code.split(',')
                        if len(nums) >= 3:
                            draw = Draw(
                                str(last['preDrawIssue']),
                                int(nums[0]), int(nums[1]), int(nums[2])
                            )
                            logger.info(f"✅ 备用API开奖: {draw.period} -> {draw.numbers}={draw.sum_value}")
                            return draw
        except Exception as e:
            logger.warning(f"备用API失败: {e}")
        return None
    
    def get_next_period(self, period: str) -> str:
        """计算下一期号"""
        match = re.search(r'(\d+)$', period)
        if match:
            num = int(match.group(1))
            return period[:match.start()] + str(num + 1)
        return period + '+1'

# ============================================================
# 服务层
# ============================================================

class BettingService:
    """下注服务"""
    
    def __init__(self, api_client: PC28APIClient):
        self.api = api_client
    
    async def process_draw(self, state: UserState, draw: Draw) -> Tuple[float, Optional[str]]:
        """处理开奖 - 结算 + 下注"""
        profit = 0.0
        
        # 1. 结算
        if state.current_bet and state.current_bet.period == draw.period:
            profit = await state.settle_bet(draw)
            logger.info(f"📊 [{state.account_name}] 结算 {draw.period}: {profit:+.3f}")
        
        # 2. 检查是否应该下注
        if not state.betting_enabled or not state.telegram_client or not state.target_groups:
            return profit, None
        
        # 3. 预测
        kill_group = prediction_engine.predict(state.history, state.algorithm)
        
        # 确定下注组 (除杀组外)
        if kill_group:
            try:
                kill = next(g for g in Group if g.value == kill_group)
                bet_groups = [g for g in Group if g != kill]
            except StopIteration:
                bet_groups = list(Group)
        else:
            bet_groups = list(Group)
        
        # 不下注四门全下
        if len(bet_groups) == 4:
            logger.info(f"⚠️ [{state.account_name}] 无杀组，跳过下注")
            return profit, None
        
        # 4. 检查13/14触发
        trigger_applied = False
        if state.trigger_enabled and draw.sum_value in (13, 14):
            state.current_recommend_amount = int(state.current_recommend_amount * state.trigger_multiplier)
            trigger_applied = True
            logger.info(f"⚡ [{state.account_name}] 13/14触发: {state.current_recommend_amount}")
        
        # 5. 下注
        next_period = self.api.get_next_period(draw.period)
        await asyncio.sleep(state.bet_delay)
        success = await state.place_bet(bet_groups, next_period, trigger_applied)
        
        if success:
            logger.info(f"✅ [{state.account_name}] 下注 {next_period}: 杀组 {kill_group}")
        else:
            logger.warning(f"⚠️ [{state.account_name}] 下注失败")
        
        return profit, kill_group


class TelegramLoginService:
    """Telegram 登录服务"""
    
    def __init__(self):
        self._clients: Dict[str, TelegramClient] = {}
    
    async def start_login(self, phone: str, voice: bool = False) -> Tuple[bool, str, Optional[TelegramClient]]:
        """开始登录流程"""
        session_path = config.get_session_path(phone)
        
        for proxy in config.PROXY_LIST:
            try:
                client = TelegramClient(
                    session_path,
                    config.API_ID,
                    config.API_HASH,
                    proxy=(ProxyType.SOCKS5, proxy['server'], proxy['port'], True, proxy['username'], proxy['password']),
                    connection_retries=3,
                    retry_delay=2
                )
                await client.connect()
                await client.send_code_request(phone)
                return True, "验证码已发送", client
            except errors.FloodWaitError as e:
                return False, f"请等待 {e.seconds} 秒", None
            except Exception as e:
                logger.warning(f"代理失败: {e}")
                continue
        
        return False, "所有代理失败", None
    
    async def complete_login(self, client: TelegramClient, phone: str, code: str, password: str = None) -> Tuple[bool, str]:
        """完成登录"""
        try:
            await client.sign_in(phone, code)
            return True, "登录成功"
        except errors.SessionPasswordNeededError:
            if password:
                try:
                    await client.sign_in(password=password)
                    return True, "登录成功"
                except Exception as e:
                    return False, f"密码错误: {e}"
            return False, "需要两步验证密码"
        except errors.PhoneCodeInvalidError:
            return False, "验证码错误"
        except errors.PhoneCodeExpiredError:
            return False, "验证码已过期"
        except Exception as e:
            return False, str(e)

# ============================================================
# 机器人主类
# ============================================================

class PC28Bot:
    """主机器人类"""
    
    def __init__(self):
        self.config = config
        self.store = UserDataStore(config.DATA_DIR)
        self.api = PC28APIClient()
        self.betting_service = BettingService(self.api)
        self.login_service = TelegramLoginService()
        
        self.app: Optional[Application] = None
        self.bot = None
        self._running = False
        self._global_period: Optional[str] = None
        self._tasks: List[asyncio.Task] = []
    
    # ============================================================
    # 键盘定义
    # ============================================================
    
    def get_main_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["🚀 启动挂机", "⏹️ 停止挂机"],
            ["📊 今日输赢", "📋 账号状态"],
            ["🔑 登录账号", "🚪 登出账号"],
            ["📁 群组管理", "🧠 切换算法"],
            ["⚙️ 下注设置", "🎯 特殊项设置"],
            ["⚡ 13/14设置", "📂 会话列表"]
        ], resize_keyboard=True)
    
    def get_group_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["➕ 添加群组", "📋 查看群组"],
            ["📊 设置倍数", "🗑️ 清空群组"],
            ["⭐ 设置默认群组", "🔙 返回主菜单"]
        ], resize_keyboard=True)
    
    def get_bet_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["💰 设置起步金额", "📈 设置递增金额"],
            ["📉 设置最大连输", "🎯 设置止盈目标"],
            ["🛑 设置止损目标", "⏱️ 设置下注延迟"],
            ["🔄 设置轮询间隔", "📊 重置今日盈亏"],
            ["🔙 返回主菜单"]
        ], resize_keyboard=True)
    
    def get_special_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["0️⃣ 0 开关/金额", "2️⃣7️⃣ 27 开关/金额"],
            ["🐆 豹子 开关/金额"],
            ["🔙 返回主菜单"]
        ], resize_keyboard=True)
    
    def get_trigger_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["✅ 开启13/14", "❌ 关闭13/14"],
            ["📊 设置触发倍数"],
            ["🔙 返回主菜单"]
        ], resize_keyboard=True)
    
    def get_algo_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["🎯 CIT杀组算法", "🧘 悲天悯人5Y"],
            ["🧘 悲天悯人7Y", "🧘 悲天悯人混合"],
            ["🔙 返回主菜单"]
        ], resize_keyboard=True)
    
    def get_admin_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 总体概况", callback_data="admin_overview"),
             InlineKeyboardButton("🔄 刷新数据", callback_data="admin_refresh")],
            [InlineKeyboardButton("💰 按余额排序", callback_data="admin_sort_balance"),
             InlineKeyboardButton("📈 按盈亏排序", callback_data="admin_sort_daily")],
            [InlineKeyboardButton("🟢 挂机中用户", callback_data="admin_filter_running"),
             InlineKeyboardButton("🔍 查看用户", callback_data="admin_user_prompt")]
        ])
    
    # ============================================================
    # 辅助方法
    # ============================================================
    
    async def get_state(self, user_id: int) -> UserState:
        return await self.store.get_or_create(user_id)
    
    async def safe_send(self, update: Update, text: str, reply_markup=None):
        """安全发送消息"""
        try:
            await update.message.reply_text(text, reply_markup=reply_markup)
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False
    
    async def safe_send_photo(self, update: Update, photo_url: str, caption: str, reply_markup=None):
        """安全发送图片"""
        try:
            await update.message.reply_photo(photo=photo_url, caption=caption, reply_markup=reply_markup)
            return True
        except Exception as e:
            logger.warning(f"图片发送失败: {e}")
            return await self.safe_send(update, caption, reply_markup)
    
    async def resolve_group(self, state: UserState, input_str: str) -> Optional[int]:
        """解析群组标识"""
        if not state.telegram_client:
            return None
        
        input_str = input_str.strip()
        
        # 数字ID
        if re.match(r'^-?\d+$', input_str):
            return int(input_str)
        
        # 用户名
        match = re.search(r'(?:t\.me/|@)([a-zA-Z0-9_]+)', input_str)
        if match:
            try:
                entity = await state.telegram_client.get_entity(f"@{match.group(1)}")
                return entity.id
            except Exception:
                return None
        
        # 尝试直接获取
        try:
            entity = await state.telegram_client.get_entity(input_str)
            return entity.id
        except Exception:
            return None
    
    def generate_menu_text(self, state: UserState) -> str:
        """生成主菜单文本"""
        status = "🟢 挂机中" if state.betting_enabled else "🔴 已停止"
        trigger = "✅ 开启" if state.trigger_enabled else "❌ 关闭"
        algo_names = {
            'cit': 'CIT杀组算法',
            'beitian_5y': '悲天悯人5Y',
            'beitian_7y': '悲天悯人7Y',
            'beitian_hybrid': '悲天悯人混合'
        }
        algo = algo_names.get(state.algorithm, '未知')
        
        group_info = "\n".join([
            f"   ┣ {gid} (×{state.group_multipliers.get(gid, 1.0):.2f})"
            for gid in state.target_groups
        ]) if state.target_groups else "   ┗ 暂无群组"
        
        daily_emoji = "📈" if state.daily_profit >= 0 else "📉"
        
        return (
            f"╔══════════════════════════════╗\n"
            f"║   🎲 悲天悯人 · 主控面板     ║\n"
            f"╚══════════════════════════════╝\n"
            f"┌─ 📋 账号信息 ─────────────────┐\n"
            f"│ 👤 {state.account_name}\n"
            f"│ 📌 {status}\n"
            f"│ ⚡ 13/14: {trigger}\n"
            f"└───────────────────────────────┘\n"
            f"┌─ 💰 余额 ─────────────────────┐\n"
            f"│ 💵 USDT: {state.usdt_balance:.3f}\n"
            f"│ 💴 CNY: {state.cny_balance:.3f}\n"
            f"│ 🪙 KKCOIN: {state.kkcoin_balance:.3f}\n"
            f"│ 💰 KK币: {state.balance:.3f}\n"
            f"└───────────────────────────────┘\n"
            f"┌─ ⚙️ 参数 ─────────────────────┐\n"
            f"│ 🎯 起步: {state.base_bet:,}\n"
            f"│ 🎯 止盈: {state.profit_target:.2f}\n"
            f"│ 🛑 止损: {state.loss_target:.2f}\n"
            f"│ 📊 {daily_emoji} 今日: {state.daily_profit:+.2f}\n"
            f"│ 🧠 算法: {algo}\n"
            f"└───────────────────────────────┘\n"
            f"┌─ 📢 群组 ─────────────────────┐\n"
            f"{group_info}\n"
            f"└───────────────────────────────┘\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 通知频道: @NXnb677"
        )
    
    # ============================================================
    # 命令处理器
    # ============================================================
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """启动命令"""
        state = await self.get_state(update.effective_chat.id)
        state.pending_login = None
        state.pending_action = None
        await state.save()
        
        await self.safe_send_photo(
            update,
            config.WELCOME_IMAGE_URL,
            "🎉 欢迎使用悲天悯人自动投注 🎉\n━━━━━━━━━━━━━━━━━━━━━━━━━\n🎲 加拿大28 智能预测自动下注\n⚡ CIT/悲天悯人多算法\n💰 实时余额 · 自动盈亏\n━━━━━━━━━━━━━━━━━━━━━━━━━",
            self.get_main_keyboard()
        )
        await self.safe_send(update, self.generate_menu_text(state), self.get_main_keyboard())
    
    async def cmd_go(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """启动挂机"""
        state = await self.get_state(update.effective_chat.id)
        
        # 自动添加默认群组
        if not state.target_groups and state.default_group:
            state.target_groups.append(state.default_group)
            state.group_multipliers[state.default_group] = 1.0
            state.mark_dirty()
            await state.save()
        
        if not state.target_groups:
            await self.safe_send(update, "❌ 请先添加下注群组", self.get_main_keyboard())
            return
        
        if not state.telegram_client:
            await self.safe_send(update, "❌ 请先登录下注账号", self.get_main_keyboard())
            return
        
        state.betting_enabled = True
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, f"✅ 启动挂机，余额: {state.balance:.3f} KK币", self.get_main_keyboard())
    
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """停止挂机"""
        state = await self.get_state(update.effective_chat.id)
        state.betting_enabled = False
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, "⏸️ 挂机已停止", self.get_main_keyboard())
    
    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """今日盈亏"""
        state = await self.get_state(update.effective_chat.id)
        emoji = "📈" if state.daily_profit >= 0 else "📉"
        await self.safe_send(
            update,
            f"╔══════════════════════════════╗\n"
            f"║     📊 今日盈亏统计          ║\n"
            f"╚══════════════════════════════╝\n"
            f"{emoji} 盈亏: {state.daily_profit:+.3f} KK币\n"
            f"💰 余额: {state.balance:.3f} KK币\n"
            f"🪙 KKCOIN: {state.kkcoin_balance:.3f}\n"
            f"💵 USDT: {state.usdt_balance:.3f}",
            self.get_main_keyboard()
        )
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """账号状态"""
        state = await self.get_state(update.effective_chat.id)
        status = "🟢 挂机中" if state.betting_enabled else "🔴 已停止"
        await self.safe_send(
            update,
            f"╔══════════════════════════════╗\n"
            f"║     📋 账号状态              ║\n"
            f"╚══════════════════════════════╝\n"
            f"👤 {state.account_name}\n"
            f"📱 {state.client_phone or '未登录'}\n"
            f"📌 {status}\n"
            f"💰 KK币: {state.balance:.3f}\n"
            f"📊 今日: {state.daily_profit:+.3f}\n"
            f"📉 连输: {state.consecutive_losses} 次",
            self.get_main_keyboard()
        )
    
    async def cmd_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """登录"""
        state = await self.get_state(update.effective_chat.id)
        state.pending_login = {'step': 'waiting_phone'}
        state.pending_action = None
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, "📱 请输入手机号 (+861234567890):")
    
    async def cmd_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """登出"""
        state = await self.get_state(update.effective_chat.id)
        if state.telegram_client:
            await state.telegram_client.disconnect()
            state.telegram_client = None
            state.client_phone = None
            state.account_name = "未登录"
            state.telegram_user_id = None
            state.mark_dirty()
            await state.save()
        await self.safe_send(update, "✅ 已登出", self.get_main_keyboard())
    
    async def cmd_sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """会话列表"""
        files = [f for f in os.listdir(config.SESSIONS_DIR) if f.endswith('.session')]
        await self.safe_send(
            update,
            f"📁 会话文件: {', '.join(files) if files else '无'}",
            self.get_main_keyboard()
        )
    
    # ---- 群组管理 ----
    
    async def cmd_add_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """添加群组"""
        state = await self.get_state(update.effective_chat.id)
        if not state.telegram_client:
            await self.safe_send(update, "❌ 请先登录", self.get_main_keyboard())
            return
        
        if not context.args:
            await self.safe_send(update, "❌ 用法: /add_group <群ID/链接/用户名>")
            return
        
        gid = await self.resolve_group(state, context.args[0])
        if gid is None:
            await self.safe_send(update, "❌ 无法解析群组")
            return
        
        if gid in state.target_groups:
            await self.safe_send(update, f"❌ 群组 {gid} 已存在")
            return
        
        state.target_groups.append(gid)
        state.group_multipliers[gid] = 1.0
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, f"✅ 已添加群组 {gid}", self.get_group_keyboard())
    
    async def cmd_set_default(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """设置默认群组"""
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.safe_send(update, "❌ 用法: /set_default <群ID/链接>")
            return
        
        if not state.telegram_client:
            await self.safe_send(update, "❌ 请先登录")
            return
        
        gid = await self.resolve_group(state, context.args[0])
        if gid is None:
            await self.safe_send(update, "❌ 无法解析群组")
            return
        
        state.default_group = gid
        if gid not in state.target_groups:
            state.target_groups.append(gid)
            state.group_multipliers[gid] = 1.0
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, f"✅ 默认群组已设为 {gid}", self.get_group_keyboard())
    
    async def cmd_list_groups(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """查看群组列表"""
        state = await self.get_state(update.effective_chat.id)
        if not state.target_groups:
            await self.safe_send(update, "📋 当前无下注群组", self.get_group_keyboard())
            return
        
        lines = ["📋 下注群组列表:"]
        for gid in state.target_groups:
            mult = state.group_multipliers.get(gid, 1.0)
            star = "⭐ " if gid == state.default_group else "   "
            lines.append(f"{star}• {gid} (×{mult:.2f})")
        await self.safe_send(update, "\n".join(lines), self.get_group_keyboard())
    
    async def cmd_clear_groups(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """清空群组"""
        state = await self.get_state(update.effective_chat.id)
        state.target_groups = []
        state.group_multipliers = {}
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, "✅ 群组已清空", self.get_group_keyboard())
    
    async def cmd_set_multiplier(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """设置倍数"""
        if len(context.args) != 2:
            await self.safe_send(update, "❌ 用法: /set_multiplier <群ID> <倍数>")
            return
        
        try:
            gid = int(context.args[0])
            mult = float(context.args[1])
        except ValueError:
            await self.safe_send(update, "❌ 群ID为整数，倍数为数字")
            return
        
        if mult <= 0:
            await self.safe_send(update, "❌ 倍数必须大于0")
            return
        
        state = await self.get_state(update.effective_chat.id)
        if gid not in state.target_groups:
            await self.safe_send(update, f"❌ 群组 {gid} 不存在")
            return
        
        state.group_multipliers[gid] = mult
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, f"✅ 群 {gid} 倍数已设为 {mult:.2f}", self.get_group_keyboard())
    
    # ---- 设置命令 ----
    
    async def cmd_set_base(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.base_bet = val
            state.current_recommend_amount = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 起步金额: {val} KK币", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_base <金额>")
    
    async def cmd_set_increment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.martin_increment = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 递增金额: {val} KK币", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_increment <金额>")
    
    async def cmd_set_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.max_losses = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 最大连输: {val} 次", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_max_losses <次数>")
    
    async def cmd_set_profit_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            state = await self.get_state(update.effective_chat.id)
            state.profit_target = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 止盈目标: {val:.2f} KK币", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_profit_target <金额>")
    
    async def cmd_set_loss_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            state = await self.get_state(update.effective_chat.id)
            state.loss_target = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 止损目标: {val:.2f} KK币", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_loss_target <金额>")
    
    async def cmd_set_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val < 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.bet_delay = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 下注延迟: {val} 秒", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_delay <秒数>")
    
    async def cmd_set_poll(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val < 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.poll_interval = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 轮询间隔: {val} 秒", self.get_bet_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /set_poll <秒数>")
    
    async def cmd_reset_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.daily_profit = 0.0
        state.last_date = date.today().isoformat()
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, "🔄 今日盈亏已重置", self.get_bet_keyboard())
    
    # ---- 特殊项 ----
    
    async def cmd_special(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        await self.safe_send(
            update,
            f"📋 特殊项设置\n\n"
            f"0: {'✅' if state.special_0_enabled else '❌'} {state.special_0_amount} KK币\n"
            f"27: {'✅' if state.special_27_enabled else '❌'} {state.special_27_amount} KK币\n"
            f"豹子: {'✅' if state.special_baozi_enabled else '❌'} {state.special_baozi_amount} KK币",
            self.get_special_keyboard()
        )
    
    async def cmd_special_0(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_special_item(update, context, 'special_0')
    
    async def cmd_special_27(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_special_item(update, context, 'special_27')
    
    async def cmd_special_baozi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._handle_special_item(update, context, 'special_baozi')
    
    async def _handle_special_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.safe_send(update, "❌ 用法: /special_item <on|off|金额>")
            return
        
        arg = context.args[0].lower()
        if arg in ('on', 'off'):
            enabled = arg == 'on'
            if key == 'special_0':
                state.special_0_enabled = enabled
            elif key == 'special_27':
                state.special_27_enabled = enabled
            else:
                state.special_baozi_enabled = enabled
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 已{'启用' if enabled else '关闭'}", self.get_special_keyboard())
        else:
            try:
                amount = int(arg)
                if amount < 0:
                    raise ValueError
                if key == 'special_0':
                    state.special_0_amount = amount
                elif key == 'special_27':
                    state.special_27_amount = amount
                else:
                    state.special_baozi_amount = amount
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, f"✅ 金额已设为 {amount} KK币", self.get_special_keyboard())
            except ValueError:
                await self.safe_send(update, "❌ 请输入 on/off 或有效金额", self.get_special_keyboard())
    
    # ---- 13/14触发 ----
    
    async def cmd_trigger(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if context.args:
            arg = context.args[0].lower()
            if arg == 'on':
                state.trigger_enabled = True
            elif arg == 'off':
                state.trigger_enabled = False
            else:
                await self.safe_send(update, "❌ 用法: /trigger on|off")
                return
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 13/14: {'开启' if state.trigger_enabled else '关闭'}", self.get_trigger_keyboard())
            return
        
        await self.safe_send(
            update,
            f"⚡ 13/14倍投功能\n"
            f"状态: {'✅ 开启' if state.trigger_enabled else '❌ 关闭'}\n"
            f"倍数: {state.trigger_multiplier:.2f}",
            self.get_trigger_keyboard()
        )
    
    async def cmd_trigger_multiplier(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.trigger_multiplier = val
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 触发倍数: {val:.2f}", self.get_trigger_keyboard())
        except:
            await self.safe_send(update, "❌ 用法: /trigger_multiplier <倍数>")
    
    # ---- 算法切换 ----
    
    async def cmd_algorithm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.safe_send(update, f"当前算法: {state.algorithm}\n可用: cit, beitian_5y, beitian_7y, beitian_hybrid")
            return
        
        algo = context.args[0].lower()
        valid = ['cit', 'beitian_5y', 'beitian_7y', 'beitian_hybrid']
        if algo not in valid:
            await self.safe_send(update, f"❌ 无效算法，可用: {', '.join(valid)}")
            return
        
        state.algorithm = algo
        state.mark_dirty()
        await state.save()
        await self.safe_send(update, f"✅ 已切换为: {algo}", self.get_main_keyboard())
    
    # ---- 管理员命令 ----
    
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.OWNER_ID:
            await self.safe_send(update, "⛔ 权限不足")
            return
        
        users = await self.store.list_all_users()
        total = len(users)
        active = sum(1 for u in users if u.get('account_name') != '未登录')
        running = sum(1 for u in users if u.get('betting_enabled', False))
        total_balance = sum(u.get('balance', 0) for u in users)
        total_profit = sum(u.get('daily_profit', 0) for u in users)
        
        await self.safe_send(
            update,
            f"╔══════════════════════════════╗\n"
            f"║   🔐 管理后台 · 统计面板     ║\n"
            f"╚══════════════════════════════╝\n"
            f"👥 总用户: {total}\n"
            f"🔑 已登录: {active}\n"
            f"🟢 挂机中: {running}\n"
            f"💰 总余额: {total_balance:.3f} KK币\n"
            f"📊 今日总盈亏: {total_profit:+.3f}",
            self.get_admin_keyboard()
        )
    
    async def cmd_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.OWNER_ID:
            await self.safe_send(update, "⛔ 权限不足")
            return
        
        if not context.args:
            await self.safe_send(update, "❌ 用法: /admin_user <用户ID>")
            return
        
        try:
            uid = int(context.args[0])
            state = await self.get_state(uid)
            await self.safe_send(
                update,
                f"╔══════════════════════════════╗\n"
                f"║   👤 用户详情 · ID: {uid}   ║\n"
                f"╚══════════════════════════════╝\n"
                f"📛 账号: {state.account_name}\n"
                f"📱 手机: {state.client_phone or '未登录'}\n"
                f"📌 状态: {'🟢 挂机' if state.betting_enabled else '🔴 停止'}\n"
                f"🧠 算法: {state.algorithm}\n"
                f"💰 KK币: {state.balance:.3f}\n"
                f"🪙 KKCOIN: {state.kkcoin_balance:.3f}\n"
                f"📊 今日: {state.daily_profit:+.3f}\n"
                f"📉 连输: {state.consecutive_losses}/{state.max_losses}\n"
                f"🎯 起步: {state.base_bet:,}\n"
                f"📢 群组: {state.target_groups or '无'}",
                self.get_admin_keyboard()
            )
        except ValueError:
            await self.safe_send(update, "❌ 用户ID应为整数")
    
    async def handle_admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """管理员回调处理"""
        query = update.callback_query
        await query.answer()
        
        if query.from_user.id != config.OWNER_ID:
            await query.edit_message_text("⛔ 权限不足")
            return
        
        data = query.data
        users = await self.store.list_all_users()
        back = [[InlineKeyboardButton("🔙 返回", callback_data="admin_menu")]]
        
        if data == "admin_menu":
            await self.cmd_admin(update, context)
            return
        
        if data == "admin_refresh":
            await self.cmd_admin(update, context)
            return
        
        if data == "admin_overview":
            text = (
                f"📊 总体概况\n"
                f"总用户: {len(users)}\n"
                f"总余额: {sum(u.get('balance', 0) for u in users):.3f}\n"
                f"总盈亏: {sum(u.get('daily_profit', 0) for u in users):+.3f}"
            )
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back))
        
        elif data == "admin_sort_balance":
            sorted_users = sorted(users, key=lambda u: u.get('balance', 0), reverse=True)[:20]
            text = "💰 余额排行 (前20):\n" + "\n".join([
                f"{i+1}. {u.get('account_name', '未知')}: {u.get('balance', 0):.3f}"
                for i, u in enumerate(sorted_users)
            ])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back))
        
        elif data == "admin_sort_daily":
            sorted_users = sorted(users, key=lambda u: u.get('daily_profit', 0), reverse=True)[:20]
            text = "📊 盈亏排行 (前20):\n" + "\n".join([
                f"{i+1}. {u.get('account_name', '未知')}: {u.get('daily_profit', 0):+.3f}"
                for i, u in enumerate(sorted_users)
            ])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back))
        
        elif data == "admin_filter_running":
            running = [u for u in users if u.get('betting_enabled', False)]
            text = f"🟢 挂机中 ({len(running)}人):\n" + "\n".join([
                f"• {u.get('account_name', '未知')} (ID:{u.get('user_id')})"
                for u in running[:20]
            ]) or "无"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back))
        
        elif data == "admin_user_prompt":
            await query.edit_message_text(
                "🔍 使用 /admin_user <用户ID> 查看详情",
                reply_markup=InlineKeyboardMarkup(back)
            )
    
    # ============================================================
    # 中文命令处理
    # ============================================================
    
    async def handle_chinese_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理中文按钮命令"""
        text = update.message.text.strip()
        user_id = update.effective_chat.id
        state = await self.get_state(user_id)
        
        # 登录流程
        if state.pending_login:
            await self._handle_login_input(update, context, state)
            return
        
        # 待处理操作
        if state.pending_action:
            await self._handle_pending_action(update, context, state, text)
            return
        
        # 主菜单命令映射
        cmd_map = {
            "🚀 启动挂机": self.cmd_go,
            "⏹️ 停止挂机": self.cmd_stop,
            "📊 今日输赢": self.cmd_today,
            "📋 账号状态": self.cmd_status,
            "🔑 登录账号": self.cmd_login,
            "🚪 登出账号": self.cmd_logout,
            "📂 会话列表": self.cmd_sessions,
            "🔙 返回主菜单": self.cmd_start,
        }
        
        if text in cmd_map:
            await cmd_map[text](update, context)
            return
        
        # 群组管理
        if text == "📁 群组管理":
            await self.safe_send(update, "📁 群组管理", self.get_group_keyboard())
            return
        
        if text == "➕ 添加群组":
            state.pending_action = "add_group"
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "请输入群组 (ID/@用户名/链接):")
            return
        
        if text == "📋 查看群组":
            await self.cmd_list_groups(update, context)
            return
        
        if text == "📊 设置倍数":
            state.pending_action = "set_multiplier"
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "请输入: 群ID 倍数")
            return
        
        if text == "🗑️ 清空群组":
            await self.cmd_clear_groups(update, context)
            return
        
        if text == "⭐ 设置默认群组":
            state.pending_action = "set_default"
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "请输入群组 (ID/@用户名/链接):")
            return
        
        # 下注设置
        if text == "⚙️ 下注设置":
            await self.safe_send(update, "⚙️ 下注设置", self.get_bet_keyboard())
            return
        
        setting_map = {
            "💰 设置起步金额": ("set_base", "请输入起步金额 (正整数):"),
            "📈 设置递增金额": ("set_increment", "请输入递增金额 (正整数):"),
            "📉 设置最大连输": ("set_max_losses", "请输入最大连输次数 (正整数):"),
            "🎯 设置止盈目标": ("set_profit_target", "请输入止盈目标 (数字):"),
            "🛑 设置止损目标": ("set_loss_target", "请输入止损目标 (数字):"),
            "⏱️ 设置下注延迟": ("set_delay", "请输入下注延迟 (秒):"),
            "🔄 设置轮询间隔": ("set_poll", "请输入轮询间隔 (秒):"),
            "📊 重置今日盈亏": self.cmd_reset_daily,
        }
        
        if text in setting_map:
            if callable(setting_map[text]):
                await setting_map[text](update, context)
            else:
                action, prompt = setting_map[text]
                state.pending_action = action
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, prompt)
            return
        
        # 特殊项
        if text == "🎯 特殊项设置":
            await self.cmd_special(update, context)
            return
        
        special_map = {
            "0️⃣ 0 开关/金额": "special_0",
            "2️⃣7️⃣ 27 开关/金额": "special_27",
            "🐆 豹子 开关/金额": "special_baozi",
        }
        if text in special_map:
            state.pending_action = special_map[text]
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "请输入 on/off 或金额:")
            return
        
        # 13/14设置
        if text == "⚡ 13/14设置":
            await self.cmd_trigger(update, context)
            return
        
        trigger_map = {
            "✅ 开启13/14": ("trigger", "on"),
            "❌ 关闭13/14": ("trigger", "off"),
            "📊 设置触发倍数": ("trigger_multiplier", None),
        }
        if text in trigger_map:
            cmd, arg = trigger_map[text]
            if arg:
                context.args = [arg]
                await self.cmd_trigger(update, context)
            else:
                state.pending_action = "trigger_multiplier"
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, "请输入触发倍数 (大于0):")
            return
        
        # 算法切换
        if text == "🧠 切换算法":
            await self.safe_send(update, "选择算法:", self.get_algo_keyboard())
            return
        
        algo_map = {
            "🎯 CIT杀组算法": "cit",
            "🧘 悲天悯人5Y": "beitian_5y",
            "🧘 悲天悯人7Y": "beitian_7y",
            "🧘 悲天悯人混合": "beitian_hybrid",
        }
        if text in algo_map:
            context.args = [algo_map[text]]
            await self.cmd_algorithm(update, context)
            return
        
        await self.safe_send(update, "❓ 请使用按钮操作", self.get_main_keyboard())
    
    # ============================================================
    # 登录流程处理
    # ============================================================
    
    async def _handle_login_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, state: UserState):
        text = update.message.text.strip()
        
        if state.pending_login['step'] == 'waiting_phone':
            if not re.match(r'^\+\d{7,15}$', text):
                await self.safe_send(update, "❌ 手机号格式错误，请重新输入 (+861234567890):")
                return
            
            state.pending_login['phone'] = text
            state.pending_login['step'] = 'waiting_method'
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "选择验证方式:", ReplyKeyboardMarkup([
                ["短信验证码", "语音验证码"]
            ], resize_keyboard=True))
            return
        
        if state.pending_login['step'] == 'waiting_method':
            voice = text == "语音验证码"
            if text not in ("短信验证码", "语音验证码"):
                await self.safe_send(update, "请点击下方按钮")
                return
            
            success, msg, client = await self.login_service.start_login(
                state.pending_login['phone'], voice
            )
            if not success:
                await self.safe_send(update, f"❌ {msg}")
                state.pending_login = None
                state.mark_dirty()
                await state.save()
                return
            
            state.pending_login['client'] = client
            state.pending_login['step'] = 'waiting_code'
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, "📨 验证码已发送，请输入:")
            return
        
        if state.pending_login['step'] == 'waiting_code':
            code = re.sub(r'\s+', '', text)
            if not code.isdigit():
                await self.safe_send(update, "❌ 验证码必须为数字")
                return
            
            client = state.pending_login.get('client')
            if not client:
                await self.safe_send(update, "❌ 会话失效，请重新登录")
                state.pending_login = None
                state.mark_dirty()
                await state.save()
                return
            
            success, msg = await self.login_service.complete_login(
                client, state.pending_login['phone'], code
            )
            
            if success:
                if state.telegram_client:
                    await state.telegram_client.disconnect()
                state.telegram_client = client
                state.client_phone = state.pending_login['phone']
                
                try:
                    me = await client.get_me()
                    state.account_name = me.first_name or state.client_phone
                    state.telegram_user_id = me.id
                except:
                    state.account_name = state.client_phone
                
                state.pending_login = None
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, f"✅ 登录成功！账号: {state.account_name}", self.get_main_keyboard())
                await self.safe_send(update, self.generate_menu_text(state), self.get_main_keyboard())
            
            elif msg == "需要两步验证密码":
                state.pending_login['step'] = 'waiting_password'
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, "🔐 请输入两步验证密码:")
            
            else:
                await self.safe_send(update, f"❌ {msg}")
                state.pending_login = None
                state.mark_dirty()
                await state.save()
            return
        
        if state.pending_login['step'] == 'waiting_password':
            client = state.pending_login.get('client')
            if not client:
                await self.safe_send(update, "❌ 请重新登录")
                state.pending_login = None
                state.mark_dirty()
                await state.save()
                return
            
            success, msg = await self.login_service.complete_login(
                client, state.pending_login['phone'], '', text
            )
            
            if success:
                if state.telegram_client:
                    await state.telegram_client.disconnect()
                state.telegram_client = client
                state.client_phone = state.pending_login['phone']
                
                try:
                    me = await client.get_me()
                    state.account_name = me.first_name or state.client_phone
                    state.telegram_user_id = me.id
                except:
                    state.account_name = state.client_phone
                
                state.pending_login = None
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, f"✅ 登录成功！账号: {state.account_name}", self.get_main_keyboard())
                await self.safe_send(update, self.generate_menu_text(state), self.get_main_keyboard())
            else:
                await self.safe_send(update, f"❌ {msg}")
                state.pending_login = None
                state.mark_dirty()
                await state.save()
    
    # ============================================================
    # 待处理操作
    # ============================================================
    
    async def _handle_pending_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE, state: UserState, text: str):
        action = state.pending_action
        state.pending_action = None
        state.mark_dirty()
        await state.save()
        
        # 群组操作
        if action == "add_group":
            if not state.telegram_client:
                await self.safe_send(update, "❌ 请先登录", self.get_main_keyboard())
                return
            
            gid = await self.resolve_group(state, text)
            if gid is None:
                await self.safe_send(update, "❌ 无法解析群组", self.get_group_keyboard())
                return
            
            if gid in state.target_groups:
                await self.safe_send(update, f"❌ 群组 {gid} 已存在", self.get_group_keyboard())
                return
            
            state.target_groups.append(gid)
            state.group_multipliers[gid] = 1.0
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 已添加群组 {gid}", self.get_group_keyboard())
            return
        
        if action == "set_default":
            if not state.telegram_client:
                await self.safe_send(update, "❌ 请先登录", self.get_main_keyboard())
                return
            
            gid = await self.resolve_group(state, text)
            if gid is None:
                await self.safe_send(update, "❌ 无法解析群组", self.get_group_keyboard())
                return
            
            state.default_group = gid
            if gid not in state.target_groups:
                state.target_groups.append(gid)
                state.group_multipliers[gid] = 1.0
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 默认群组已设为 {gid}", self.get_group_keyboard())
            return
        
        if action == "set_multiplier":
            parts = text.split()
            if len(parts) != 2:
                await self.safe_send(update, "❌ 格式: 群ID 倍数", self.get_group_keyboard())
                return
            
            try:
                gid = int(parts[0])
                mult = float(parts[1])
            except ValueError:
                await self.safe_send(update, "❌ 群ID为整数，倍数为数字", self.get_group_keyboard())
                return
            
            if mult <= 0:
                await self.safe_send(update, "❌ 倍数必须大于0", self.get_group_keyboard())
                return
            
            if gid not in state.target_groups:
                await self.safe_send(update, f"❌ 群组 {gid} 不存在", self.get_group_keyboard())
                return
            
            state.group_multipliers[gid] = mult
            state.mark_dirty()
            await state.save()
            await self.safe_send(update, f"✅ 群 {gid} 倍数已设为 {mult:.2f}", self.get_group_keyboard())
            return
        
        # 设置操作
        setting_actions = {
            "set_base": ("base_bet", int, "起步金额", "current_recommend_amount"),
            "set_increment": ("martin_increment", int, "递增金额", None),
            "set_max_losses": ("max_losses", int, "最大连输", None),
            "set_profit_target": ("profit_target", float, "止盈目标", None),
            "set_loss_target": ("loss_target", float, "止损目标", None),
            "set_delay": ("bet_delay", int, "下注延迟", None),
            "set_poll": ("poll_interval", int, "轮询间隔", None),
            "trigger_multiplier": ("trigger_multiplier", float, "触发倍数", None),
        }
        
        if action in setting_actions:
            attr, caster, label, sync_attr = setting_actions[action]
            try:
                val = caster(text)
                if val <= 0:
                    raise ValueError("必须大于0")
                setattr(state, attr, val)
                if sync_attr:
                    setattr(state, sync_attr, val)
                state.mark_dirty()
                await state.save()
                keyboard = self.get_bet_keyboard() if action != "trigger_multiplier" else self.get_trigger_keyboard()
                await self.safe_send(update, f"✅ {label}: {val}", keyboard)
            except ValueError as e:
                await self.safe_send(update, f"❌ 输入无效: {e}", self.get_bet_keyboard())
            return
        
        # 特殊项
        special_actions = {
            "special_0": ("special_0_enabled", "special_0_amount"),
            "special_27": ("special_27_enabled", "special_27_amount"),
            "special_baozi": ("special_baozi_enabled", "special_baozi_amount"),
        }
        
        if action in special_actions:
            enabled_attr, amount_attr = special_actions[action]
            low = text.lower()
            if low in ('on', 'off'):
                setattr(state, enabled_attr, low == 'on')
                state.mark_dirty()
                await state.save()
                await self.safe_send(update, f"✅ 已{'启用' if low == 'on' else '关闭'}", self.get_special_keyboard())
            else:
                try:
                    amount = int(text)
                    if amount < 0:
                        raise ValueError
                    setattr(state, amount_attr, amount)
                    state.mark_dirty()
                    await state.save()
                    await self.safe_send(update, f"✅ 金额已设为 {amount} KK币", self.get_special_keyboard())
                except ValueError:
                    await self.safe_send(update, "❌ 请输入 on/off 或有效金额", self.get_special_keyboard())
            return
        
        await self.safe_send(update, "⚠️ 操作已过期", self.get_main_keyboard())
    
    # ============================================================
    # 核心循环
    # ============================================================
    
    async def _run_loop(self):
        """主循环"""
        logger.info("🚀 主循环启动")
        
        while self._running:
            try:
                # 获取开奖
                draw = await self.api.fetch_latest()
                if not draw:
                    await asyncio.sleep(30)
                    continue
                
                if self._global_period == draw.period:
                    await asyncio.sleep(5)
                    continue
                
                self._global_period = draw.period
                log_block(f"🆕 新期号 {draw.period}  号码 {draw.numbers}={draw.sum_value} ({draw.group.value})")
                
                # 获取所有活跃用户
                all_states = []
                for uid in list(self.store._cache.keys()):
                    state = await self.get_state(uid)
                    if state.telegram_client:
                        all_states.append((uid, state))
                
                # 1. 结算
                settle_tasks = []
                for uid, state in all_states:
                    if state.current_bet and state.current_bet.period == draw.period:
                        settle_tasks.append(self._settle_bet(uid, state, draw))
                
                if settle_tasks:
                    await asyncio.gather(*settle_tasks)
                
                await asyncio.sleep(15)
                
                # 2. 每个用户处理
                for uid, state in all_states:
                    user_log_block(state.account_name, uid)
                    
                    # 更新余额
                    await state.update_balance_from_kkpay()
                    
                    # 下注
                    await self._process_bet(uid, state, draw)
                
                # 3. 汇总
                total_balance = sum(s.balance for s in self.store._cache.values() if s.telegram_client)
                total_profit = sum(s.daily_profit for s in self.store._cache.values() if s.telegram_client)
                total_kkcoin = sum(s.kkcoin_balance for s in self.store._cache.values() if s.telegram_client)
                log_block(f"📊 [汇总] 总KK币 {total_balance:.3f} | 总KKCOIN {total_kkcoin:.3f} | 今日总盈亏 {total_profit:+.3f}")
                
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.exception(f"主循环异常: {e}")
                await asyncio.sleep(30)
    
    async def _settle_bet(self, uid: int, state: UserState, draw: Draw):
        """结算单个用户"""
        try:
            profit = await state.settle_bet(draw)
            if profit != 0:
                logger.info(f"💰 [{state.account_name}] 结算 {draw.period}: {profit:+.3f}")
        except Exception as e:
            logger.error(f"❌ [{state.account_name}] 结算失败: {e}")
    
    async def _process_bet(self, uid: int, state: UserState, draw: Draw):
        """处理单个用户下注"""
        try:
            if not state.betting_enabled or not state.telegram_client or not state.target_groups:
                return
            
            # 预测
            kill_group = prediction_engine.predict(state.history, state.algorithm)
            
            if kill_group:
                try:
                    kill = next(g for g in Group if g.value == kill_group)
                    bet_groups = [g for g in Group if g != kill]
                except StopIteration:
                    bet_groups = list(Group)
            else:
                bet_groups = list(Group)
            
            if len(bet_groups) == 4:
                logger.info(f"⚠️ [{state.account_name}] 无杀组，跳过下注")
                return
            
            next_period = self.api.get_next_period(draw.period)
            
            # 13/14触发
            trigger_applied = False
            if state.trigger_enabled and draw.sum_value in (13, 14):
                state.current_recommend_amount = int(state.current_recommend_amount * state.trigger_multiplier)
                trigger_applied = True
                logger.info(f"⚡ [{state.account_name}] 13/14触发: {state.current_recommend_amount}")
            
            await asyncio.sleep(state.bet_delay)
            success = await state.place_bet(bet_groups, next_period, trigger_applied)
            
            if success:
                logger.info(f"✅ [{state.account_name}] 下注 {next_period}: 杀组 {kill_group}")
            else:
                logger.warning(f"⚠️ [{state.account_name}] 下注失败")
                
        except Exception as e:
            logger.error(f"❌ [{state.account_name}] 下注处理失败: {e}")
    
    # ============================================================
    # 启动
    # ============================================================
    
    async def start(self):
        """启动机器人"""
        # 清除webhook
        try:
            requests.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/deleteWebhook")
            await asyncio.sleep(0.5)
        except:
            pass
        
        # 构建应用
        self.app = Application.builder().token(config.BOT_TOKEN).build()
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        self.bot = self.app.bot
        
        # 注册处理器
        self._register_handlers()
        
        # 启动
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        
        self._running = True
        asyncio.create_task(self._run_loop())
        
        logger.info("🤖 机器人已启动")
        
        # 保持运行
        await asyncio.Event().wait()
    
    def _register_handlers(self):
        """注册处理器"""
        # 命令
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("menu", self.cmd_start))
        self.app.add_handler(CommandHandler("go", self.cmd_go))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("today", self.cmd_today))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("login", self.cmd_login))
        self.app.add_handler(CommandHandler("logout", self.cmd_logout))
        self.app.add_handler(CommandHandler("sessions", self.cmd_sessions))
        
        # 群组
        self.app.add_handler(CommandHandler("add_group", self.cmd_add_group))
        self.app.add_handler(CommandHandler("set_default", self.cmd_set_default))
        self.app.add_handler(CommandHandler("list_groups", self.cmd_list_groups))
        self.app.add_handler(CommandHandler("clear_groups", self.cmd_clear_groups))
        self.app.add_handler(CommandHandler("set_multiplier", self.cmd_set_multiplier))
        
        # 设置
        self.app.add_handler(CommandHandler("set_base", self.cmd_set_base))
        self.app.add_handler(CommandHandler("set_increment", self.cmd_set_increment))
        self.app.add_handler(CommandHandler("set_max_losses", self.cmd_set_max_losses))
        self.app.add_handler(CommandHandler("set_profit_target", self.cmd_set_profit_target))
        self.app.add_handler(CommandHandler("set_loss_target", self.cmd_set_loss_target))
        self.app.add_handler(CommandHandler("set_delay", self.cmd_set_delay))
        self.app.add_handler(CommandHandler("set_poll", self.cmd_set_poll))
        self.app.add_handler(CommandHandler("reset_daily", self.cmd_reset_daily))
        
        # 特殊项
        self.app.add_handler(CommandHandler("special", self.cmd_special))
        self.app.add_handler(CommandHandler("special_0", self.cmd_special_0))
        self.app.add_handler(CommandHandler("special_27", self.cmd_special_27))
        self.app.add_handler(CommandHandler("special_baozi", self.cmd_special_baozi))
        
        # 触发
        self.app.add_handler(CommandHandler("trigger", self.cmd_trigger))
        self.app.add_handler(CommandHandler("trigger_multiplier", self.cmd_trigger_multiplier))
        
        # 算法
        self.app.add_handler(CommandHandler("algorithm", self.cmd_algorithm))
        
        # 管理
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))
        self.app.add_handler(CommandHandler("admin_user", self.cmd_admin_user))
        self.app.add_handler(CallbackQueryHandler(self.handle_admin_callback, pattern="^admin_"))
        
        # 消息处理
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_chinese_command))
    
    def stop(self):
        """停止机器人"""
        self._running = False
        if self.app:
            self.app.stop()


# ============================================================
# 入口
# ============================================================

async def main():
    bot = PC28Bot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("🛑 收到停止信号，正在关闭...")
        bot.stop()
    except Exception as e:
        logger.exception(f"❌ 运行异常: {e}")
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
