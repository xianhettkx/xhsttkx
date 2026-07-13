#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 自动下注机器人 - 小鶴神 v3.0
简约版 - 无 Emoji，紧凑信息
"""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from collections import Counter

import requests
from telethon import TelegramClient, errors
from python_socks import ProxyType
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes
)

# ============================================================
# 配置
# ============================================================

class Config:
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
        
        # API 配置
        self.API_ID = 2040
        self.API_HASH = 'b18441a1ff607e10a989891a85d152c4'
        self.BOT_TOKEN = '8987076623:AAGYfKZMcv-ox10XVpYmpfoTPyoInQgWgLg'
        self.OWNER_ID = 1047239922
        
        # 不使用代理
        self.PROXY_LIST = []
        
        # 默认参数
        self.DEFAULT_BASE_BET = 60000
        self.DEFAULT_MARTIN_INCREMENT = 100000
        self.DEFAULT_MAX_LOSSES = 10
        self.DEFAULT_POLL_INTERVAL = 30
        self.DEFAULT_BET_DELAY = 15
        self.DEFAULT_TRIGGER_MULTIPLIER = 2.0
        self.DEFAULT_SPECIAL_AMOUNT = 10000
        
        # 赔率
        self.ODDS = {
            'small_odd_big_even': 4.72,
            'small_even_big_odd': 4.32,
            'special_0_27': 4.72,
            'special_baozi': 10.0,
        }
        
        # 目录
        self.DATA_DIR = "user_data"
        self.SESSIONS_DIR = "telegram_sessions"
        self.WELCOME_IMAGE_URL = "https://free.boltp.com/2026/07/13/6a541b59a069a.webp"
        self.BOT_NAME = "小鶴神"
        
        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
    
    def get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace('+', 'plus').replace(' ', '')
        return os.path.join(self.SESSIONS_DIR, f"{safe_phone}.session")

config = Config()

# ============================================================
# 日志
# ============================================================

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s')
logger = logging.getLogger("XiaoHeShen")

# ============================================================
# 数据模型
# ============================================================

class Group(Enum):
    SMALL_ODD = "小单"
    SMALL_EVEN = "小双"
    BIG_ODD = "大单"
    BIG_EVEN = "大双"
    
    @staticmethod
    def from_sum(v: int) -> 'Group':
        small = v <= 13
        odd = v % 2 == 1
        if small and odd:
            return Group.SMALL_ODD
        elif small and not odd:
            return Group.SMALL_EVEN
        elif not small and odd:
            return Group.BIG_ODD
        else:
            return Group.BIG_EVEN


@dataclass
class Draw:
    period: str
    hundreds: int
    tens: int
    ones: int
    
    @property
    def sum(self) -> int:
        return self.hundreds + self.tens + self.ones
    
    @property
    def group(self) -> Group:
        return Group.from_sum(self.sum)
    
    @property
    def is_baozi(self) -> bool:
        return self.hundreds == self.tens == self.ones
    
    @property
    def nums(self) -> str:
        return f"{self.hundreds}+{self.tens}+{self.ones}"
    
    def to_dict(self) -> Dict:
        return {'period': self.period, 'hundreds': self.hundreds, 'tens': self.tens, 'ones': self.ones}
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'Draw':
        return cls(d['period'], d['hundreds'], d['tens'], d['ones'])


@dataclass
class BetItem:
    name: str
    amount: int
    odds: float


@dataclass
class BetRecord:
    period: str
    items: List[BetItem]
    group_multipliers: Dict[int, float]
    trigger_applied: bool = False
    
    def calc_profit(self, draw: Draw) -> float:
        total = 0.0
        for item in self.items:
            win = self._is_win(item.name, draw)
            total += item.amount * item.odds - item.amount if win else -item.amount
        return total
    
    @staticmethod
    def _is_win(name: str, draw: Draw) -> bool:
        if name in ("小单", "小双", "大单", "大双"):
            return draw.group.value == name
        if name == "0":
            return draw.sum == 0
        if name == "27":
            return draw.sum == 27
        if name == "豹子":
            return draw.is_baozi
        return False
    
    def to_dict(self) -> Dict:
        return {
            'period': self.period,
            'items': [{'name': i.name, 'amount': i.amount, 'odds': i.odds} for i in self.items],
            'group_multipliers': self.group_multipliers,
            'trigger_applied': self.trigger_applied,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'BetRecord':
        items = [BetItem(**i) for i in d['items']]
        return cls(d['period'], items, d['group_multipliers'], d.get('trigger_applied', False))

# ============================================================
# 存储
# ============================================================

class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._cache = {}
        self._lock = asyncio.Lock()
    
    def _path(self, uid: int) -> str:
        return os.path.join(self.data_dir, f"user_{uid}.json")
    
    async def load(self, uid: int) -> Optional[Dict]:
        path = self._path(uid)
        if not os.path.exists(path):
            return None
        try:
            async with self._lock:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载用户 {uid} 失败: {e}")
            return None
    
    async def save(self, uid: int, data: Dict):
        path = self._path(uid)
        try:
            async with self._lock:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存用户 {uid} 失败: {e}")
    
    async def get(self, uid: int):
        if uid in self._cache:
            return self._cache[uid]
        state = UserState(uid, self)
        await state.load()
        self._cache[uid] = state
        return state
    
    async def list_all(self) -> List[Dict]:
        users = []
        for fname in os.listdir(self.data_dir):
            if not fname.startswith("user_") or not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.data_dir, fname), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    uid = int(fname.split("_")[1].split(".")[0])
                    users.append({'user_id': uid, **data})
            except:
                continue
        return users

# ============================================================
# 用户状态
# ============================================================

class UserState:
    def __init__(self, uid: int, store: Store):
        self.uid = uid
        self._store = store
        self._dirty = False
        
        self.name = "未登录"
        self.phone = None
        self.tg_uid = None
        self.client = None
        
        self.groups = []
        self.multipliers = {}
        self.default_group = None
        
        self.balance = 0.0
        self.last_balance = 0.0
        self.usdt = 0.0
        self.cny = 0.0
        self.kkcoin = 0.0
        
        self.daily_profit = 0.0
        self.last_date = date.today().isoformat()
        self.profit_target = 0.0
        self.loss_target = 0.0
        
        self.base_bet = config.DEFAULT_BASE_BET
        self.increment = config.DEFAULT_MARTIN_INCREMENT
        self.max_losses = config.DEFAULT_MAX_LOSSES
        self.bet_delay = config.DEFAULT_BET_DELAY
        self.poll_interval = config.DEFAULT_POLL_INTERVAL
        self.trigger_enabled = True
        self.trigger_multiplier = config.DEFAULT_TRIGGER_MULTIPLIER
        
        self.special_0_enabled = True
        self.special_0_amount = config.DEFAULT_SPECIAL_AMOUNT
        self.special_27_enabled = True
        self.special_27_amount = config.DEFAULT_SPECIAL_AMOUNT
        self.special_baozi_enabled = True
        self.special_baozi_amount = config.DEFAULT_SPECIAL_AMOUNT
        
        self.algorithm = "cit"
        self.betting = False
        self.losses = 0
        self.recommend_amount = config.DEFAULT_BASE_BET
        self.last_period = None
        self.history = []
        self.current_bet = None
        self.pending_login = None
        self.pending_action = None
    
    async def load(self):
        data = await self._store.load(self.uid)
        if not data:
            return
        for k, v in data.items():
            if k == 'history':
                self.history = [Draw.from_dict(d) for d in v]
            elif k == 'current_bet' and v:
                self.current_bet = BetRecord.from_dict(v)
            elif hasattr(self, k):
                setattr(self, k, v)
        self._dirty = False
    
    async def save(self):
        if not self._dirty:
            return
        data = {
            'name': self.name, 'phone': self.phone, 'tg_uid': self.tg_uid,
            'groups': self.groups, 'multipliers': self.multipliers, 'default_group': self.default_group,
            'balance': self.balance, 'last_balance': self.last_balance,
            'usdt': self.usdt, 'cny': self.cny, 'kkcoin': self.kkcoin,
            'daily_profit': self.daily_profit, 'last_date': self.last_date,
            'profit_target': self.profit_target, 'loss_target': self.loss_target,
            'base_bet': self.base_bet, 'increment': self.increment,
            'max_losses': self.max_losses, 'bet_delay': self.bet_delay,
            'poll_interval': self.poll_interval,
            'trigger_enabled': self.trigger_enabled, 'trigger_multiplier': self.trigger_multiplier,
            'special_0_enabled': self.special_0_enabled, 'special_0_amount': self.special_0_amount,
            'special_27_enabled': self.special_27_enabled, 'special_27_amount': self.special_27_amount,
            'special_baozi_enabled': self.special_baozi_enabled, 'special_baozi_amount': self.special_baozi_amount,
            'algorithm': self.algorithm, 'betting': self.betting,
            'losses': self.losses, 'recommend_amount': self.recommend_amount,
            'last_period': self.last_period,
            'history': [d.to_dict() for d in self.history[-200:]],
            'current_bet': self.current_bet.to_dict() if self.current_bet else None,
        }
        await self._store.save(self.uid, data)
        self._dirty = False
    
    def mark(self):
        self._dirty = True
    
    def special_items(self) -> List[BetItem]:
        items = []
        if self.special_0_enabled:
            items.append(BetItem("0", self.special_0_amount, config.ODDS['special_0_27']))
        if self.special_27_enabled:
            items.append(BetItem("27", self.special_27_amount, config.ODDS['special_0_27']))
        if self.special_baozi_enabled:
            items.append(BetItem("豹子", self.special_baozi_amount, config.ODDS['special_baozi']))
        return items
    
    def get_odds(self, g: Group) -> float:
        return config.ODDS['small_odd_big_even'] if g in (Group.SMALL_ODD, Group.BIG_EVEN) else config.ODDS['small_even_big_odd']
    
    def reset_daily(self):
        today = date.today().isoformat()
        if self.last_date != today:
            self.daily_profit = 0.0
            self.last_date = today
            self.mark()
    
    def add_profit(self, profit: float):
        self.reset_daily()
        self.daily_profit += profit
        self.balance += profit
        self.last_balance = self.balance
        self.mark()
    
    async def settle(self, draw: Draw) -> float:
        if not self.current_bet:
            return 0.0
        profit = self.current_bet.calc_profit(draw)
        self.add_profit(profit)
        if profit > 0:
            self.losses = 0
            self.recommend_amount = self.base_bet
        else:
            self.losses += 1
            if not self.current_bet.trigger_applied:
                self.recommend_amount += self.increment
            if self.losses >= self.max_losses:
                self.recommend_amount = self.base_bet
                self.losses = 0
        self.last_period = draw.period
        self.current_bet = None
        self.mark()
        await self.save()
        return profit
    
    async def place_bet(self, groups: List[Group], next_period: str, triggered: bool = False) -> bool:
        if not self.client or not self.groups:
            return False
        items = []
        for g in groups:
            items.append(BetItem(g.value, self.recommend_amount, self.get_odds(g)))
        items.extend(self.special_items())
        if not items:
            return False
        for gid in self.groups:
            mult = self.multipliers.get(gid, 1.0)
            parts = [f"{i.name} {int(i.amount * mult)}" for i in items if int(i.amount * mult) > 0]
            if not parts:
                continue
            msg = " ".join(parts)
            try:
                await self.client.send_message(gid, msg)
                logger.info(f"[{self.name}] 下注到 {gid}: {msg[:80]}")
            except Exception as e:
                logger.error(f"[{self.name}] 下注失败 {gid}: {e}")
                return False
        self.current_bet = BetRecord(next_period, items, {gid: self.multipliers.get(gid, 1.0) for gid in self.groups}, triggered)
        self.mark()
        await self.save()
        return True
    
    async def update_balance(self):
        if not self.client:
            return
        try:
            await self.client.send_message('kkpay', '/start')
            await asyncio.sleep(3)
            msgs = await self.client.get_messages('kkpay', limit=5)
            for msg in msgs:
                if not msg.text:
                    continue
                usdt = self._parse_currency(msg.text, 'USDT')
                cny = self._parse_currency(msg.text, 'CNY')
                kkcoin = self._parse_currency(msg.text, 'KKCOIN')
                if kkcoin is not None:
                    self.usdt = usdt or 0.0
                    self.cny = cny or 0.0
                    self.kkcoin = kkcoin
                    profit = kkcoin - self.last_balance
                    self.balance = kkcoin
                    self.last_balance = kkcoin
                    self.reset_daily()
                    self.daily_profit += profit
                    self.mark()
                    await self.save()
                    logger.info(f"[{self.name}] 余额: KKCOIN={kkcoin:.3f} 盈亏={profit:+.3f}")
                    break
        except Exception as e:
            logger.error(f"[{self.name}] 余额查询失败: {e}")
    
    @staticmethod
    def _parse_currency(text: str, currency: str) -> Optional[float]:
        m = re.search(rf'{currency}\s*[:：]\s*([\d.]+)', text, re.IGNORECASE)
        return float(m.group(1)) if m else None

# ============================================================
# 预测引擎
# ============================================================

class Predictor:
    def predict(self, history: List[Draw], algo: str) -> Optional[str]:
        if len(history) < 2:
            return None
        if algo == 'cit':
            return self._cit(history)
        return self._beitian(history)
    
    def _cit(self, history: List[Draw]) -> Optional[str]:
        if len(history) < 5:
            return '小单'
        recent = history[-10:]
        groups = [d.group.value for d in recent]
        totals = [d.sum for d in recent]
        all_g = ['小单', '小双', '大单', '大双']
        counts = Counter(groups)
        scores = {g: 0 for g in all_g}
        for g in all_g:
            scores[g] += (10 - counts.get(g, 0)) * 3
        if len(groups) >= 2:
            streak = 1
            for i in range(len(groups)-2, -1, -1):
                if groups[i] == groups[-1]:
                    streak += 1
                else:
                    break
            if streak >= 3:
                scores[groups[-1]] -= streak * 15
        return min(scores, key=scores.get)
    
    def _beitian(self, history: List[Draw]) -> Optional[str]:
        if len(history) < 3:
            return None
        recent = history[-5:]
        groups = [d.group.value for d in recent]
        counts = Counter(groups)
        all_g = ['小单', '小双', '大单', '大双']
        for g in all_g:
            if g not in counts:
                return g
        return min(counts, key=counts.get)

predictor = Predictor()

# ============================================================
# API 客户端
# ============================================================

class APIClient:
    async def fetch(self) -> Optional[Draw]:
        draw = await self._fetch_primary()
        if draw:
            return draw
        return await self._fetch_backup()
    
    async def _fetch_primary(self) -> Optional[Draw]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.get("https://pc28.help/api/kj.json", timeout=10))
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
                            d = Draw(str(period), digits[0], digits[1], digits[2])
                            logger.info(f"API开奖: {d.period} -> {d.nums}={d.sum}")
                            return d
        except Exception as e:
            logger.warning(f"主API失败: {e}")
        return None
    
    async def _fetch_backup(self) -> Optional[Draw]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: requests.get(
                "https://api.api68.com/pks/getLotteryInfo.do?date=&lotCode=10026", timeout=10
            ))
            if resp.status_code == 200:
                data = resp.json()
                if data.get('result') and data['result']['data']:
                    last = data['result']['data'][0]
                    code = last.get('preDrawCode')
                    if code:
                        nums = code.split(',')
                        if len(nums) >= 3:
                            d = Draw(str(last['preDrawIssue']), int(nums[0]), int(nums[1]), int(nums[2]))
                            logger.info(f"备用API开奖: {d.period} -> {d.nums}={d.sum}")
                            return d
        except Exception as e:
            logger.warning(f"备用API失败: {e}")
        return None
    
    def next_period(self, period: str) -> str:
        m = re.search(r'(\d+)$', period)
        if m:
            num = int(m.group(1))
            return period[:m.start()] + str(num + 1)
        return period + '+1'

# ============================================================
# 登录服务
# ============================================================

class LoginService:
    async def start(self, phone: str) -> Tuple[bool, str, Optional[TelegramClient]]:
        path = config.get_session_path(phone)
        try:
            client = TelegramClient(path, config.API_ID, config.API_HASH, connection_retries=5, retry_delay=2, timeout=30)
            await client.connect()
            await client.send_code_request(phone)
            return True, "验证码已发送", client
        except errors.FloodWaitError as e:
            return False, f"请等待 {e.seconds} 秒", None
        except Exception as e:
            return False, f"连接失败: {str(e)[:50]}", None
    
    async def complete(self, client: TelegramClient, phone: str, code: str, password: str = None) -> Tuple[bool, str]:
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
# 主机器人 - 简约版
# ============================================================

class XiaoHeShenBot:
    def __init__(self):
        self.store = Store(config.DATA_DIR)
        self.api = APIClient()
        self.login = LoginService()
        self.app = None
        self.bot = None
        self._running = False
        self._global_period = None
    
    # ---- 键盘 ----
    
    def main_keyboard(self):
        return ReplyKeyboardMarkup([
            ["启动挂机", "停止挂机"],
            ["今日输赢", "账号状态"],
            ["登录账号", "登出账号"],
            ["群组管理", "切换算法"],
            ["下注设置", "特殊项"],
            ["13/14设置", "会话列表"]
        ], resize_keyboard=True)
    
    def group_keyboard(self):
        return ReplyKeyboardMarkup([
            ["添加群组", "查看群组"],
            ["设置倍数", "清空群组"],
            ["默认群组", "返回主菜单"]
        ], resize_keyboard=True)
    
    def bet_keyboard(self):
        return ReplyKeyboardMarkup([
            ["起步金额", "递增金额"],
            ["最大连输", "止盈目标"],
            ["止损目标", "下注延迟"],
            ["轮询间隔", "重置盈亏"],
            ["返回主菜单"]
        ], resize_keyboard=True)
    
    def special_keyboard(self):
        return ReplyKeyboardMarkup([
            ["0设置", "27设置"],
            ["豹子设置"],
            ["返回主菜单"]
        ], resize_keyboard=True)
    
    def trigger_keyboard(self):
        return ReplyKeyboardMarkup([
            ["开启13/14", "关闭13/14"],
            ["触发倍数"],
            ["返回主菜单"]
        ], resize_keyboard=True)
    
    def algo_keyboard(self):
        return ReplyKeyboardMarkup([
            ["CIT杀组", "悲天悯人5Y"],
            ["悲天悯人7Y", "悲天悯人混合"],
            ["返回主菜单"]
        ], resize_keyboard=True)
    
    # ---- 辅助 ----
    
    async def get_state(self, uid: int) -> UserState:
        return await self.store.get(uid)
    
    async def send(self, update: Update, text: str, kb=None):
        try:
            await update.message.reply_text(text, reply_markup=kb)
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False
    
    async def resolve_group(self, state: UserState, s: str) -> Optional[int]:
        if not state.client:
            return None
        s = s.strip()
        if re.match(r'^-?\d+$', s):
            return int(s)
        m = re.search(r'(?:t\.me/|@)([a-zA-Z0-9_]+)', s)
        if m:
            try:
                e = await state.client.get_entity(f"@{m.group(1)}")
                return e.id
            except:
                return None
        try:
            e = await state.client.get_entity(s)
            return e.id
        except:
            return None
    
    # ---- 菜单 ----
    
    def welcome_text(self) -> str:
        return (
            "小鶴神自动投注 v3.0\n"
            "--------------------\n"
            "加拿大28 智能预测\n"
            "CIT / 悲天悯人 算法\n"
            "实时余额追踪"
        )
    
    def menu_text(self, state: UserState) -> str:
        status = "运行中" if state.betting else "已停止"
        trigger = "开" if state.trigger_enabled else "关"
        algo_names = {'cit': 'CIT', 'beitian_5y': '悲天5Y', 'beitian_7y': '悲天7Y', 'beitian_hybrid': '悲天混合'}
        algo = algo_names.get(state.algorithm, '未知')
        
        groups = "\n".join([f"  {gid} x{state.multipliers.get(gid, 1.0):.2f}" for gid in state.groups]) if state.groups else "  无"
        daily = f"+{state.daily_profit:.2f}" if state.daily_profit >= 0 else f"{state.daily_profit:.2f}"
        
        return (
            f"[小鶴神] 控制面板\n"
            f"--------------------\n"
            f"账号: {state.name}\n"
            f"状态: {status}\n"
            f"13/14: {trigger}\n"
            f"--------------------\n"
            f"USDT: {state.usdt:.3f}\n"
            f"CNY: {state.cny:.3f}\n"
            f"KKCOIN: {state.kkcoin:.3f}\n"
            f"余额: {state.balance:.3f}\n"
            f"--------------------\n"
            f"起步: {state.base_bet:,}\n"
            f"止盈: {state.profit_target:.2f}\n"
            f"止损: {state.loss_target:.2f}\n"
            f"今日: {daily}\n"
            f"算法: {algo}\n"
            f"--------------------\n"
            f"群组:\n{groups}"
        )
    
    # ---- 命令 ----
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.pending_login = None
        state.pending_action = None
        await state.save()
        await self.send(update, self.welcome_text(), self.main_keyboard())
        await self.send(update, self.menu_text(state), self.main_keyboard())
    
    async def cmd_go(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not state.groups and state.default_group:
            state.groups.append(state.default_group)
            state.multipliers[state.default_group] = 1.0
            state.mark()
            await state.save()
        if not state.groups:
            await self.send(update, "错误: 没有下注群组", self.main_keyboard())
            return
        if not state.client:
            await self.send(update, "错误: 请先登录", self.main_keyboard())
            return
        state.betting = True
        state.mark()
        await state.save()
        await self.send(update, f"已启动，余额: {state.balance:.3f}", self.main_keyboard())
    
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.betting = False
        state.mark()
        await state.save()
        await self.send(update, "已停止", self.main_keyboard())
    
    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        sign = "+" if state.daily_profit >= 0 else ""
        await self.send(
            update,
            f"[今日盈亏]\n"
            f"盈亏: {sign}{state.daily_profit:.3f}\n"
            f"余额: {state.balance:.3f}\n"
            f"KKCOIN: {state.kkcoin:.3f}\n"
            f"USDT: {state.usdt:.3f}",
            self.main_keyboard()
        )
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        status = "运行中" if state.betting else "已停止"
        await self.send(
            update,
            f"[账号状态]\n"
            f"名称: {state.name}\n"
            f"手机: {state.phone or '未登录'}\n"
            f"状态: {status}\n"
            f"余额: {state.balance:.3f}\n"
            f"今日: {state.daily_profit:+.3f}\n"
            f"连输: {state.losses}",
            self.main_keyboard()
        )
    
    async def cmd_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.pending_login = {'step': 'phone'}
        state.pending_action = None
        state.mark()
        await state.save()
        await self.send(update, "请输入手机号 (+861234567890):")
    
    async def cmd_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if state.client:
            await state.client.disconnect()
            state.client = None
            state.phone = None
            state.name = "未登录"
            state.tg_uid = None
            state.mark()
            await state.save()
        await self.send(update, "已登出", self.main_keyboard())
    
    async def cmd_sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        files = [f for f in os.listdir(config.SESSIONS_DIR) if f.endswith('.session')]
        await self.send(update, f"会话: {', '.join(files) if files else '无'}", self.main_keyboard())
    
    # ---- 群组 ----
    
    async def cmd_add_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not state.client:
            await self.send(update, "错误: 请先登录", self.main_keyboard())
            return
        if not context.args:
            await self.send(update, "用法: /add_group <群ID/链接>")
            return
        gid = await self.resolve_group(state, context.args[0])
        if gid is None:
            await self.send(update, "错误: 无法解析群组")
            return
        if gid in state.groups:
            await self.send(update, f"群组 {gid} 已存在")
            return
        state.groups.append(gid)
        state.multipliers[gid] = 1.0
        state.mark()
        await state.save()
        await self.send(update, f"已添加群组 {gid}", self.group_keyboard())
    
    async def cmd_default_group(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.send(update, "用法: /default_group <群ID/链接>")
            return
        if not state.client:
            await self.send(update, "错误: 请先登录")
            return
        gid = await self.resolve_group(state, context.args[0])
        if gid is None:
            await self.send(update, "错误: 无法解析群组")
            return
        state.default_group = gid
        if gid not in state.groups:
            state.groups.append(gid)
            state.multipliers[gid] = 1.0
        state.mark()
        await state.save()
        await self.send(update, f"默认群组: {gid}", self.group_keyboard())
    
    async def cmd_list_groups(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not state.groups:
            await self.send(update, "无群组", self.group_keyboard())
            return
        lines = ["群组列表:"]
        for gid in state.groups:
            mult = state.multipliers.get(gid, 1.0)
            star = "* " if gid == state.default_group else "  "
            lines.append(f"{star}{gid} x{mult:.2f}")
        await self.send(update, "\n".join(lines), self.group_keyboard())
    
    async def cmd_clear_groups(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.groups = []
        state.multipliers = {}
        state.mark()
        await state.save()
        await self.send(update, "群组已清空", self.group_keyboard())
    
    async def cmd_set_multiplier(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 2:
            await self.send(update, "用法: /set_multiplier <群ID> <倍数>")
            return
        try:
            gid = int(context.args[0])
            mult = float(context.args[1])
        except ValueError:
            await self.send(update, "错误: 格式无效")
            return
        if mult <= 0:
            await self.send(update, "错误: 倍数必须大于0")
            return
        state = await self.get_state(update.effective_chat.id)
        if gid not in state.groups:
            await self.send(update, f"群组 {gid} 不存在")
            return
        state.multipliers[gid] = mult
        state.mark()
        await state.save()
        await self.send(update, f"群组 {gid} x{mult:.2f}", self.group_keyboard())
    
    # ---- 设置 ----
    
    async def cmd_set_base(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.base_bet = val
            state.recommend_amount = val
            state.mark()
            await state.save()
            await self.send(update, f"起步金额: {val}", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_base <金额>")
    
    async def cmd_set_increment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.increment = val
            state.mark()
            await state.save()
            await self.send(update, f"递增金额: {val}", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_increment <金额>")
    
    async def cmd_set_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.max_losses = val
            state.mark()
            await state.save()
            await self.send(update, f"最大连输: {val}", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_max_losses <次数>")
    
    async def cmd_set_profit_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            state = await self.get_state(update.effective_chat.id)
            state.profit_target = val
            state.mark()
            await state.save()
            await self.send(update, f"止盈目标: {val:.2f}", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_profit_target <金额>")
    
    async def cmd_set_loss_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            state = await self.get_state(update.effective_chat.id)
            state.loss_target = val
            state.mark()
            await state.save()
            await self.send(update, f"止损目标: {val:.2f}", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_loss_target <金额>")
    
    async def cmd_set_delay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val < 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.bet_delay = val
            state.mark()
            await state.save()
            await self.send(update, f"下注延迟: {val}秒", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_delay <秒数>")
    
    async def cmd_set_poll(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(context.args[0])
            if val < 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.poll_interval = val
            state.mark()
            await state.save()
            await self.send(update, f"轮询间隔: {val}秒", self.bet_keyboard())
        except:
            await self.send(update, "用法: /set_poll <秒数>")
    
    async def cmd_reset_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        state.daily_profit = 0.0
        state.last_date = date.today().isoformat()
        state.mark()
        await state.save()
        await self.send(update, "今日盈亏已重置", self.bet_keyboard())
    
    # ---- 特殊项 ----
    
    async def cmd_special(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        await self.send(
            update,
            f"[特殊项]\n"
            f"0: {'开' if state.special_0_enabled else '关'} {state.special_0_amount}\n"
            f"27: {'开' if state.special_27_enabled else '关'} {state.special_27_amount}\n"
            f"豹子: {'开' if state.special_baozi_enabled else '关'} {state.special_baozi_amount}",
            self.special_keyboard()
        )
    
    async def _special_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.send(update, "用法: /item <on|off|金额>")
            return
        arg = context.args[0].lower()
        if arg in ('on', 'off'):
            enabled = arg == 'on'
            if key == '0':
                state.special_0_enabled = enabled
            elif key == '27':
                state.special_27_enabled = enabled
            else:
                state.special_baozi_enabled = enabled
            state.mark()
            await state.save()
            await self.send(update, f"特殊项 {key}: {'开' if enabled else '关'}", self.special_keyboard())
        else:
            try:
                amount = int(arg)
                if amount < 0:
                    raise ValueError
                if key == '0':
                    state.special_0_amount = amount
                elif key == '27':
                    state.special_27_amount = amount
                else:
                    state.special_baozi_amount = amount
                state.mark()
                await state.save()
                await self.send(update, f"特殊项 {key}: {amount}", self.special_keyboard())
            except ValueError:
                await self.send(update, "错误: 金额无效", self.special_keyboard())
    
    async def cmd_special_0(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._special_item(update, context, '0')
    
    async def cmd_special_27(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._special_item(update, context, '27')
    
    async def cmd_special_baozi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._special_item(update, context, 'baozi')
    
    # ---- 13/14 ----
    
    async def cmd_trigger(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if context.args:
            arg = context.args[0].lower()
            if arg == 'on':
                state.trigger_enabled = True
            elif arg == 'off':
                state.trigger_enabled = False
            else:
                await self.send(update, "用法: /trigger on|off")
                return
            state.mark()
            await state.save()
            await self.send(update, f"13/14: {'开' if state.trigger_enabled else '关'}", self.trigger_keyboard())
            return
        await self.send(
            update,
            f"[13/14触发]\n"
            f"状态: {'开' if state.trigger_enabled else '关'}\n"
            f"倍数: {state.trigger_multiplier:.2f}",
            self.trigger_keyboard()
        )
    
    async def cmd_trigger_multiplier(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(context.args[0])
            if val <= 0:
                raise ValueError
            state = await self.get_state(update.effective_chat.id)
            state.trigger_multiplier = val
            state.mark()
            await state.save()
            await self.send(update, f"触发倍数: {val:.2f}", self.trigger_keyboard())
        except:
            await self.send(update, "用法: /trigger_multiplier <倍数>")
    
    # ---- 算法 ----
    
    async def cmd_algorithm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = await self.get_state(update.effective_chat.id)
        if not context.args:
            await self.send(update, f"当前: {state.algorithm}\n可用: cit, beitian_5y, beitian_7y, beitian_hybrid")
            return
        algo = context.args[0].lower()
        valid = ['cit', 'beitian_5y', 'beitian_7y', 'beitian_hybrid']
        if algo not in valid:
            await self.send(update, f"无效. 可用: {', '.join(valid)}")
            return
        state.algorithm = algo
        state.mark()
        await state.save()
        await self.send(update, f"算法: {algo}", self.main_keyboard())
    
    # ---- 管理员 ----
    
    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.OWNER_ID:
            await self.send(update, "权限不足")
            return
        users = await self.store.list_all()
        total = len(users)
        active = sum(1 for u in users if u.get('name') != '未登录')
        running = sum(1 for u in users if u.get('betting', False))
        total_bal = sum(u.get('balance', 0) for u in users)
        total_profit = sum(u.get('daily_profit', 0) for u in users)
        await self.send(
            update,
            f"[管理后台]\n"
            f"用户: {total}\n"
            f"已登录: {active}\n"
            f"运行中: {running}\n"
            f"总余额: {total_bal:.3f}\n"
            f"总盈亏: {total_profit:+.3f}"
        )
    
    async def cmd_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.OWNER_ID:
            await self.send(update, "权限不足")
            return
        if not context.args:
            await self.send(update, "用法: /admin_user <用户ID>")
            return
        try:
            uid = int(context.args[0])
            state = await self.get_state(uid)
            await self.send(
                update,
                f"[用户详情] ID: {uid}\n"
                f"名称: {state.name}\n"
                f"手机: {state.phone or '未登录'}\n"
                f"状态: {'运行中' if state.betting else '已停止'}\n"
                f"算法: {state.algorithm}\n"
                f"余额: {state.balance:.3f}\n"
                f"KKCOIN: {state.kkcoin:.3f}\n"
                f"今日: {state.daily_profit:+.3f}\n"
                f"连输: {state.losses}/{state.max_losses}\n"
                f"起步: {state.base_bet:,}\n"
                f"群组: {state.groups or '无'}"
            )
        except ValueError:
            await self.send(update, "错误: 无效的用户ID")
    
    # ---- 处理中文 ----
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        uid = update.effective_chat.id
        state = await self.get_state(uid)
        
        if state.pending_login:
            await self._handle_login(update, context, state)
            return
        
        if state.pending_action:
            await self._handle_action(update, context, state, text)
            return
        
        cmd_map = {
            "启动挂机": self.cmd_go,
            "停止挂机": self.cmd_stop,
            "今日输赢": self.cmd_today,
            "账号状态": self.cmd_status,
            "登录账号": self.cmd_login,
            "登出账号": self.cmd_logout,
            "会话列表": self.cmd_sessions,
            "返回主菜单": self.cmd_start,
        }
        if text in cmd_map:
            await cmd_map[text](update, context)
            return
        
        if text == "群组管理":
            await self.send(update, "群组管理", self.group_keyboard())
            return
        if text == "添加群组":
            state.pending_action = "add_group"
            state.mark()
            await state.save()
            await self.send(update, "请输入群组 (ID/@用户名/链接):")
            return
        if text == "查看群组":
            await self.cmd_list_groups(update, context)
            return
        if text == "设置倍数":
            state.pending_action = "set_multiplier"
            state.mark()
            await state.save()
            await self.send(update, "请输入: 群ID 倍数")
            return
        if text == "清空群组":
            await self.cmd_clear_groups(update, context)
            return
        if text == "默认群组":
            state.pending_action = "default_group"
            state.mark()
            await state.save()
            await self.send(update, "请输入群组 (ID/@用户名/链接):")
            return
        
        if text == "下注设置":
            await self.send(update, "下注设置", self.bet_keyboard())
            return
        
        setting_map = {
            "起步金额": ("set_base", "请输入起步金额:"),
            "递增金额": ("set_increment", "请输入递增金额:"),
            "最大连输": ("set_max_losses", "请输入最大连输次数:"),
            "止盈目标": ("set_profit_target", "请输入止盈目标:"),
            "止损目标": ("set_loss_target", "请输入止损目标:"),
            "下注延迟": ("set_delay", "请输入下注延迟(秒):"),
            "轮询间隔": ("set_poll", "请输入轮询间隔(秒):"),
            "重置盈亏": self.cmd_reset_daily,
        }
        if text in setting_map:
            if callable(setting_map[text]):
                await setting_map[text](update, context)
            else:
                action, prompt = setting_map[text]
                state.pending_action = action
                state.mark()
                await state.save()
                await self.send(update, prompt)
            return
        
        if text == "特殊项":
            await self.cmd_special(update, context)
            return
        if text in ("0设置", "27设置", "豹子设置"):
            mapping = {"0设置": "special_0", "27设置": "special_27", "豹子设置": "special_baozi"}
            state.pending_action = mapping[text]
            state.mark()
            await state.save()
            await self.send(update, "请输入 on/off 或金额:")
            return
        
        if text == "13/14设置":
            await self.cmd_trigger(update, context)
            return
        if text in ("开启13/14", "关闭13/14"):
            context.args = ["on" if "开启" in text else "off"]
            await self.cmd_trigger(update, context)
            return
        if text == "触发倍数":
            state.pending_action = "trigger_multiplier"
            state.mark()
            await state.save()
            await self.send(update, "请输入触发倍数:")
            return
        
        if text == "切换算法":
            await self.send(update, "选择算法:", self.algo_keyboard())
            return
        algo_map = {
            "CIT杀组": "cit",
            "悲天悯人5Y": "beitian_5y",
            "悲天悯人7Y": "beitian_7y",
            "悲天悯人混合": "beitian_hybrid",
        }
        if text in algo_map:
            context.args = [algo_map[text]]
            await self.cmd_algorithm(update, context)
            return
        
        await self.send(update, "未知命令，请使用按钮", self.main_keyboard())
    
    # ---- 登录处理 ----
    
    async def _handle_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE, state: UserState):
        text = update.message.text.strip()
        
        if state.pending_login['step'] == 'phone':
            if not re.match(r'^\+\d{7,15}$', text):
                await self.send(update, "手机号格式错误，请输入 +861234567890:")
                return
            state.pending_login['phone'] = text
            state.pending_login['step'] = 'code'
            state.mark()
            await state.save()
            await self.send(update, "请输入验证码:")
            return
        
        if state.pending_login['step'] == 'code':
            code = re.sub(r'\s+', '', text)
            if not code.isdigit():
                await self.send(update, "验证码必须为数字")
                return
            
            phone = state.pending_login['phone']
            success, msg, client = await self.login.start(phone)
            if not success:
                await self.send(update, f"错误: {msg}")
                state.pending_login = None
                state.mark()
                await state.save()
                return
            
            success, msg = await self.login.complete(client, phone, code)
            if success:
                if state.client:
                    await state.client.disconnect()
                state.client = client
                state.phone = phone
                try:
                    me = await client.get_me()
                    state.name = me.first_name or phone
                    state.tg_uid = me.id
                except:
                    state.name = phone
                state.pending_login = None
                state.mark()
                await state.save()
                await self.send(update, f"登录成功: {state.name}", self.main_keyboard())
                await self.send(update, self.menu_text(state), self.main_keyboard())
            else:
                await self.send(update, f"错误: {msg}")
                state.pending_login = None
                state.mark()
                await state.save()
    
    # ---- 动作处理 ----
    
    async def _handle_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE, state: UserState, text: str):
        action = state.pending_action
        state.pending_action = None
        state.mark()
        await state.save()
        
        if action == "add_group":
            if not state.client:
                await self.send(update, "错误: 请先登录", self.main_keyboard())
                return
            gid = await self.resolve_group(state, text)
            if gid is None:
                await self.send(update, "错误: 无法解析群组", self.group_keyboard())
                return
            if gid in state.groups:
                await self.send(update, f"群组 {gid} 已存在", self.group_keyboard())
                return
            state.groups.append(gid)
            state.multipliers[gid] = 1.0
            state.mark()
            await state.save()
            await self.send(update, f"已添加群组 {gid}", self.group_keyboard())
            return
        
        if action == "default_group":
            if not state.client:
                await self.send(update, "错误: 请先登录", self.main_keyboard())
                return
            gid = await self.resolve_group(state, text)
            if gid is None:
                await self.send(update, "错误: 无法解析群组", self.group_keyboard())
                return
            state.default_group = gid
            if gid not in state.groups:
                state.groups.append(gid)
                state.multipliers[gid] = 1.0
            state.mark()
            await state.save()
            await self.send(update, f"默认群组: {gid}", self.group_keyboard())
            return
        
        if action == "set_multiplier":
            parts = text.split()
            if len(parts) != 2:
                await self.send(update, "格式: 群ID 倍数", self.group_keyboard())
                return
            try:
                gid = int(parts[0])
                mult = float(parts[1])
            except ValueError:
                await self.send(update, "错误: 格式无效", self.group_keyboard())
                return
            if mult <= 0:
                await self.send(update, "错误: 倍数必须大于0", self.group_keyboard())
                return
            if gid not in state.groups:
                await self.send(update, f"群组 {gid} 不存在", self.group_keyboard())
                return
            state.multipliers[gid] = mult
            state.mark()
            await state.save()
            await self.send(update, f"群组 {gid} x{mult:.2f}", self.group_keyboard())
            return
        
        set_actions = {
            "set_base": ("base_bet", int, "起步金额", "recommend_amount"),
            "set_increment": ("increment", int, "递增金额", None),
            "set_max_losses": ("max_losses", int, "最大连输", None),
            "set_profit_target": ("profit_target", float, "止盈目标", None),
            "set_loss_target": ("loss_target", float, "止损目标", None),
            "set_delay": ("bet_delay", int, "下注延迟", None),
            "set_poll": ("poll_interval", int, "轮询间隔", None),
            "trigger_multiplier": ("trigger_multiplier", float, "触发倍数", None),
        }
        if action in set_actions:
            attr, caster, label, sync = set_actions[action]
            try:
                val = caster(text)
                if val <= 0:
                    raise ValueError
                setattr(state, attr, val)
                if sync:
                    setattr(state, sync, val)
                state.mark()
                await state.save()
                kb = self.bet_keyboard() if action != "trigger_multiplier" else self.trigger_keyboard()
                await self.send(update, f"{label}: {val}", kb)
            except ValueError:
                await self.send(update, "错误: 值无效", self.bet_keyboard())
            return
        
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
                state.mark()
                await state.save()
                await self.send(update, f"特殊项: {'开' if low == 'on' else '关'}", self.special_keyboard())
            else:
                try:
                    amount = int(text)
                    if amount < 0:
                        raise ValueError
                    setattr(state, amount_attr, amount)
                    state.mark()
                    await state.save()
                    await self.send(update, f"金额: {amount}", self.special_keyboard())
                except ValueError:
                    await self.send(update, "错误: 金额无效", self.special_keyboard())
            return
        
        await self.send(update, "操作已过期", self.main_keyboard())
    
    # ---- 主循环 ----
    
    async def run_loop(self):
        logger.info("主循环启动")
        while self._running:
            try:
                draw = await self.api.fetch()
                if not draw:
                    await asyncio.sleep(30)
                    continue
                if self._global_period == draw.period:
                    await asyncio.sleep(5)
                    continue
                self._global_period = draw.period
                logger.info(f"新期号: {draw.period} {draw.nums}={draw.sum}")
                
                states = []
                for uid in list(self.store._cache.keys()):
                    state = await self.store.get(uid)
                    if state.client:
                        states.append((uid, state))
                
                # 结算
                for uid, state in states:
                    if state.current_bet and state.current_bet.period == draw.period:
                        try:
                            profit = await state.settle(draw)
                            if profit != 0:
                                logger.info(f"[{state.name}] 结算 {draw.period}: {profit:+.3f}")
                        except Exception as e:
                            logger.error(f"[{state.name}] 结算失败: {e}")
                
                await asyncio.sleep(15)
                
                # 下注
                for uid, state in states:
                    try:
                        if not state.betting or not state.client or not state.groups:
                            continue
                        await state.update_balance()
                        kill = predictor.predict(state.history, state.algorithm)
                        if kill:
                            try:
                                kill_g = next(g for g in Group if g.value == kill)
                                bet_groups = [g for g in Group if g != kill_g]
                            except StopIteration:
                                bet_groups = list(Group)
                        else:
                            bet_groups = list(Group)
                        if len(bet_groups) == 4:
                            continue
                        next_period = self.api.next_period(draw.period)
                        triggered = False
                        if state.trigger_enabled and draw.sum in (13, 14):
                            state.recommend_amount = int(state.recommend_amount * state.trigger_multiplier)
                            triggered = True
                            logger.info(f"[{state.name}] 13/14触发: {state.recommend_amount}")
                        await asyncio.sleep(state.bet_delay)
                        success = await state.place_bet(bet_groups, next_period, triggered)
                        if success:
                            logger.info(f"[{state.name}] 下注 {next_period}: 杀组={kill}")
                    except Exception as e:
                        logger.error(f"[{state.name}] 下注失败: {e}")
                
                total_bal = sum(s.balance for s in self.store._cache.values() if s.client)
                total_profit = sum(s.daily_profit for s in self.store._cache.values() if s.client)
                logger.info(f"汇总: 余额={total_bal:.3f} 盈亏={total_profit:+.3f}")
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.exception(f"循环错误: {e}")
                await asyncio.sleep(30)
    
    # ---- 启动 ----
    
    async def start(self):
        try:
            requests.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/deleteWebhook")
            await asyncio.sleep(0.5)
        except:
            pass
        
        self.app = Application.builder().token(config.BOT_TOKEN).build()
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        self.bot = self.app.bot
        
        # 注册命令
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("menu", self.cmd_start))
        self.app.add_handler(CommandHandler("go", self.cmd_go))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("today", self.cmd_today))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("login", self.cmd_login))
        self.app.add_handler(CommandHandler("logout", self.cmd_logout))
        self.app.add_handler(CommandHandler("sessions", self.cmd_sessions))
        self.app.add_handler(CommandHandler("add_group", self.cmd_add_group))
        self.app.add_handler(CommandHandler("default_group", self.cmd_default_group))
        self.app.add_handler(CommandHandler("list_groups", self.cmd_list_groups))
        self.app.add_handler(CommandHandler("clear_groups", self.cmd_clear_groups))
        self.app.add_handler(CommandHandler("set_multiplier", self.cmd_set_multiplier))
        self.app.add_handler(CommandHandler("set_base", self.cmd_set_base))
        self.app.add_handler(CommandHandler("set_increment", self.cmd_set_increment))
        self.app.add_handler(CommandHandler("set_max_losses", self.cmd_set_max_losses))
        self.app.add_handler(CommandHandler("set_profit_target", self.cmd_set_profit_target))
        self.app.add_handler(CommandHandler("set_loss_target", self.cmd_set_loss_target))
        self.app.add_handler(CommandHandler("set_delay", self.cmd_set_delay))
        self.app.add_handler(CommandHandler("set_poll", self.cmd_set_poll))
        self.app.add_handler(CommandHandler("reset_daily", self.cmd_reset_daily))
        self.app.add_handler(CommandHandler("special", self.cmd_special))
        self.app.add_handler(CommandHandler("special_0", self.cmd_special_0))
        self.app.add_handler(CommandHandler("special_27", self.cmd_special_27))
        self.app.add_handler(CommandHandler("special_baozi", self.cmd_special_baozi))
        self.app.add_handler(CommandHandler("trigger", self.cmd_trigger))
        self.app.add_handler(CommandHandler("trigger_multiplier", self.cmd_trigger_multiplier))
        self.app.add_handler(CommandHandler("algorithm", self.cmd_algorithm))
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))
        self.app.add_handler(CommandHandler("admin_user", self.cmd_admin_user))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        
        self._running = True
        asyncio.create_task(self.run_loop())
        
        logger.info("机器人已启动")
        await asyncio.Event().wait()
    
    def stop(self):
        self._running = False
        if self.app:
            self.app.stop()

# ============================================================
# 入口
# ============================================================

async def main():
    bot = XiaoHeShenBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("正在停止...")
        bot.stop()
    except Exception as e:
        logger.exception(f"错误: {e}")
        bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
