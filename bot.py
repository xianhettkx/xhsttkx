#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小鶴神 · 自动投注机器人 v3.0
基于自投.py的登录逻辑重构
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
from pathlib import Path

# ===== 修复：添加 aiohttp 导入 =====
import aiohttp
import requests
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
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
        self.API_HASH = 'b18441a1ff607e10a989891a5462e627'
        self.BOT_TOKEN = '8987076623:AAGYfKZMcv-ox10XVpYmpfoTPyoInQgWgLg'
        self.OWNER_ID = 1047239922
        
        # 不使用代理
        self.PROXY_LIST = []
        
        # 默认参数
        self.DEFAULT_BASE_BET = 60000
        self.DEFAULT_MARTIN_INCREMENT = 100000
        self.DEFAULT_MAX_LOSSES = 10
        self.DEFAULT_POLL_INTERVAL = 30
        self.DEFAULT_BET_DELAY = 50
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
        
        os.makedirs(self.DATA_DIR, exist_ok=True)
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
    
    def get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace('+', 'plus').replace(' ', '')
        return os.path.join(self.SESSIONS_DIR, f"{safe_phone}.session")

config = Config()

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s'
)
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
            return draw.hundreds == draw.tens == draw.ones
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
        self.client_phone = None
        
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
        
        self.algorithm = "hybrid"
        self.betting = False
        self.losses = 0
        self.recommend_amount = config.DEFAULT_BASE_BET
        self.last_period = None
        self.history = []
        self.current_bet = None
        self.pending_login = None
        self.pending_action = None
        
        # 算法胜率统计
        self.algo_stats = {}
        self.last_kill = None
        self.last_predicted_algo = None
    
    async def load(self):
        data = await self._store.load(self.uid)
        if not data:
            return
        for k, v in data.items():
            if k == 'history':
                self.history = [Draw.from_dict(d) for d in v]
            elif k == 'current_bet' and v:
                self.current_bet = BetRecord.from_dict(v)
            elif k == 'algo_stats':
                self.algo_stats = v
            elif hasattr(self, k):
                setattr(self, k, v)
        self._dirty = False
    
    async def save(self):
        if not self._dirty:
            return
        data = {
            'name': self.name, 'phone': self.phone, 'tg_uid': self.tg_uid,
            'client_phone': self.client_phone,
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
            'algo_stats': self.algo_stats,
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
    
    def record_prediction(self, algo: str, kill: str):
        """记录预测"""
        algo_names = {
            '5y': '悲天5Y', '7y': '悲天7Y',
            'hybrid': '悲天混合', 'stats': '统计规律',
            'all': '全部融合', 'cit': 'CIT杀组'
        }
        
        if algo not in self.algo_stats:
            self.algo_stats[algo] = {
                "win": 0,
                "total": 0,
                "name": algo_names.get(algo, algo)
            }
        
        self.algo_stats[algo]["total"] += 1
        self.last_kill = kill
        self.last_predicted_algo = algo
        self.mark()
    
    def record_result(self, actual: str):
        """记录预测结果"""
        if not self.last_kill or not self.last_predicted_algo:
            return
        
        algo = self.last_predicted_algo
        if algo in self.algo_stats:
            if self.last_kill == actual:
                self.algo_stats[algo]["win"] += 1
            # 计算胜率
            stats = self.algo_stats[algo]
            if stats["total"] > 0:
                stats["rate"] = round(stats["win"] / stats["total"] * 100, 2)
            self.mark()
        
        self.last_kill = None
        self.last_predicted_algo = None
    
    async def settle(self, draw: Draw) -> float:
        if not self.current_bet:
            return 0.0
        profit = self.current_bet.calc_profit(draw)
        self.add_profit(profit)
        
        # 记录预测结果
        self.record_result(draw.group.value)
        
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
            except FloodWaitError as e:
                logger.warning(f"[{self.name}] Flood wait {e.seconds}s")
                await asyncio.sleep(e.seconds)
                return False
            except Exception as e:
                logger.error(f"[{self.name}] 下注失败 {gid}: {e}")
                return False
        self.current_bet = BetRecord(next_period, items, {gid: self.multipliers.get(gid, 1.0) for gid in self.groups}, triggered)
        self.mark()
        await self.save()
        return True

# ============================================================
# 预测引擎
# ============================================================

class BeitianPredictor:
    def __init__(self):
        self.history = []
        self.yu5_rules = {0: 'shi', 1: 'ge', 2: 'bai', 3: 'bai_shi', 4: 'ge'}
        self.yu7_rules = {0: 'shi', 1: 'ge', 2: 'bai', 3: 'bai_shi', 4: 'ge', 5: 'shi', 6: 'bai'}
        self.yu5_kill = {0: '小单', 1: '大单', 2: '小双', 3: '大双', 4: '小单'}
        self.yu7_kill = {0: '小单', 1: '大单', 2: '小双', 3: '大双', 4: '小单', 5: '小双', 6: '小单'}

    def add_data(self, history):
        self.history = []
        for item in history:
            if isinstance(item, Draw):
                total = item.sum
                nums = [item.hundreds, item.tens, item.ones]
            elif isinstance(item, dict):
                total = item.get('sum', 0)
                n = item.get('number', '0+0+0')
                nums = [int(x) for x in n.split('+')] if '+' in n and len(n.split('+')) == 3 else [0, 0, 0]
            else:
                continue
            self.history.append({'total': total, 'nums': nums, 'yu5': total % 5, 'yu7': total % 7})

    def predict_5y(self):
        if len(self.history) < 2:
            return '小单'
        latest = self.history[-1]
        yu5 = latest['yu5']
        cn = latest['nums']
        refs = [d for d in self.history[:-1] if d['yu5'] == yu5][-4:]
        if not refs:
            return self.yu5_kill.get(yu5, '小单')
        kills = []
        for ref in refs:
            rule = self.yu5_rules.get(yu5, 'ge')
            nn = cn.copy()
            if rule == 'bai':
                nn[0] = (cn[0] + ref['nums'][0]) % 10
            elif rule == 'shi':
                nn[1] = (cn[1] + ref['nums'][1]) % 10
            elif rule == 'ge':
                nn[2] = (cn[2] + ref['nums'][2]) % 10
            elif rule == 'bai_shi':
                n = (cn[0] + cn[1] + ref['nums'][0] + ref['nums'][1]) % 10
                nn[0] = n
                nn[1] = n
            kills.append(self.yu5_kill.get(sum(nn) % 5, '小单'))
        return Counter(kills).most_common(1)[0][0]

    def predict_7y(self):
        if len(self.history) < 2:
            return '小单'
        latest = self.history[-1]
        yu7 = latest['yu7']
        cn = latest['nums']
        refs = [d for d in self.history[:-1] if d['yu7'] == yu7][-4:]
        if not refs:
            return self.yu7_kill.get(yu7, '小单')
        kills = []
        for ref in refs:
            rule = self.yu7_rules.get(yu7, 'ge')
            nn = cn.copy()
            if rule == 'bai':
                nn[0] = (cn[0] + ref['nums'][0]) % 10
            elif rule == 'shi':
                nn[1] = (cn[1] + ref['nums'][1]) % 10
            elif rule == 'ge':
                nn[2] = (cn[2] + ref['nums'][2]) % 10
            elif rule == 'bai_shi':
                n = (cn[0] + cn[1] + ref['nums'][0] + ref['nums'][1]) % 10
                nn[0] = n
                nn[1] = n
            kills.append(self.yu7_kill.get(sum(nn) % 7, '小单'))
        return Counter(kills).most_common(1)[0][0]

    def predict(self):
        k5 = self.predict_5y()
        k7 = self.predict_7y()
        return k5 if k5 == k7 else k5


def predict_stats(history):
    if len(history) < 5:
        return '小单'
    combos = ['小单', '小双', '大单', '大双']
    groups = []
    totals = []
    for h in history[-10:]:
        if isinstance(h, Draw):
            groups.append(h.group.value)
            totals.append(h.sum)
        elif isinstance(h, dict):
            groups.append(h.get('combo', '小单'))
            totals.append(h.get('sum', 0))
    if not groups:
        return '小单'
    
    scores = {g: 0 for g in combos}
    streak = 1
    for i in range(len(groups)-2, -1, -1):
        if groups[i] == groups[-1]:
            streak += 1
        else:
            break
    if streak >= 3:
        scores[groups[-1]] -= streak * 15
    cnt = Counter(groups)
    for g in combos:
        scores[g] += (10 - cnt.get(g, 0)) * 3
    for g in combos:
        miss = 0
        for i in range(len(groups)-1, -1, -1):
            if groups[i] == g:
                break
            miss += 1
        scores[g] += miss * 2
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[-1][0]


def predict_kill(history, algo):
    if not history:
        return '小单'
    
    if algo in ['5y', '7y', 'hybrid']:
        bt = BeitianPredictor()
        bt.add_data(history)
        if algo == '5y':
            return bt.predict_5y()
        elif algo == '7y':
            return bt.predict_7y()
        else:
            return bt.predict()
    elif algo == 'stats':
        return predict_stats(history)
    elif algo == 'all':
        bt = BeitianPredictor()
        bt.add_data(history)
        k5 = bt.predict_5y()
        k7 = bt.predict_7y()
        ks = predict_stats(history)
        votes = [k5, k7, ks]
        return Counter(votes).most_common(1)[0][0]
    return '小单'

# ============================================================
# API 客户端
# ============================================================

class APIClient:
    async def fetch(self, nbr=100) -> List[Draw]:
        """获取历史开奖数据"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://pc28.help/api/kj.json?nbr={nbr}", timeout=15) as resp:
                    data = await resp.json()
                    if data.get('message') == 'success':
                        items = data.get('data', [])
                        history = []
                        for item in items:
                            period = item.get('nbr', '')
                            num_str = item.get('number') or item.get('num', '')
                            if not num_str or '+' not in num_str:
                                continue
                            parts = num_str.split('+')
                            if len(parts) != 3:
                                continue
                            try:
                                digits = [int(x) for x in parts]
                            except ValueError:
                                continue
                            draw = Draw(period, digits[0], digits[1], digits[2])
                            history.append(draw)
                        history.sort(key=lambda x: x.period, reverse=True)
                        return history
        except Exception as e:
            logger.warning(f"API获取失败: {e}")
        return []
    
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
    async def login(self, phone: str) -> Tuple[bool, str, Optional[TelegramClient]]:
        session_path = config.get_session_path(phone)
        
        try:
            client = TelegramClient(session_path, config.API_ID, config.API_HASH)
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                return True, f"已登录: {me.first_name}", client
            
            await client.send_code_request(phone)
            return True, "验证码已发送", client
            
        except FloodWaitError as e:
            return False, f"请等待 {e.seconds} 秒", None
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False, f"连接失败: {str(e)[:50]}", None
    
    async def complete_login(self, client: TelegramClient, phone: str, code: str, password: str = None) -> Tuple[bool, str]:
        try:
            await client.sign_in(phone=phone, code=code)
            me = await client.get_me()
            return True, f"登录成功: {me.first_name}"
        except SessionPasswordNeededError:
            if password:
                try:
                    await client.sign_in(password=password)
                    me = await client.get_me()
                    return True, f"登录成功: {me.first_name}"
                except Exception as e:
                    return False, f"密码错误: {e}"
            return False, "需要两步验证密码"
        except Exception as e:
            return False, str(e)

# ============================================================
# 主机器人
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
            ["13/14设置", "算法排行榜"],
            ["会话列表"]
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
            ["悲天悯人5Y", "悲天悯人7Y"],
            ["悲天悯人混合", "统计规律"],
            ["全部融合", "返回主菜单"]
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
            "小鶴神 · 自动投注 v3.0\n"
            "--------------------\n"
            "加拿大28 智能预测\n"
            "悲天悯人 / 统计融合\n"
            "实时自动下注"
        )
    
    def menu_text(self, state: UserState) -> str:
        status = "运行中" if state.betting else "已停止"
        trigger = "开" if state.trigger_enabled else "关"
        algo_names = {
            '5y': '悲天5Y', '7y': '悲天7Y',
            'hybrid': '悲天混合', 'stats': '统计规律',
            'all': '全部融合'
        }
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
            state.client_phone = None
            state.name = "未登录"
            state.tg_uid = None
            state.mark()
            await state.save()
        await self.send(update, "已登出", self.main_keyboard())
    
    async def cmd_sessions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        files = [f for f in os.listdir(config.SESSIONS_DIR) if f.endswith('.session')]
        await self.send(update, f"会话: {', '.join(files) if files else '无'}", self.main_keyboard())
    
    # ---- 算法排行榜 ----
    
    async def cmd_rank(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """算法胜率排行榜"""
        state = await self.get_state(update.effective_chat.id)
        
        if not state.algo_stats:
            await self.send(update, "暂无数据，请先运行挂机", self.main_keyboard())
            return
        
        # 按胜率排序
        sorted_algos = sorted(
            state.algo_stats.items(),
            key=lambda x: x[1].get("rate", 0),
            reverse=True
        )
        
        lines = ["[算法胜率排行榜]"]
        lines.append("━" * 25)
        
        for i, (algo, stats) in enumerate(sorted_algos, 1):
            name = stats.get("name", algo)
            rate = stats.get("rate", 0)
            win = stats.get("win", 0)
            total = stats.get("total", 0)
            
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} {name}")
            lines.append(f"   胜率: {rate:.1f}% ({win}/{total})")
        
        lines.append("━" * 25)
        total_predictions = sum(s.get("total", 0) for _, s in sorted_algos)
        lines.append(f"📊 总预测次数: {total_predictions}")
        
        await self.send(update, "\n".join(lines), self.main_keyboard())
    
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
            await self.send(update, f"当前: {state.algorithm}\n可用: 5y, 7y, hybrid, stats, all")
            return
        algo = context.args[0].lower()
        valid = ['5y', '7y', 'hybrid', 'stats', 'all']
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
            "算法排行榜": self.cmd_rank,
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
            "悲天悯人5Y": "5y",
            "悲天悯人7Y": "7y",
            "悲天悯人混合": "hybrid",
            "统计规律": "stats",
            "全部融合": "all",
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
            
            success, msg, client = await self.login.login(text)
            if not success:
                await self.send(update, f"错误: {msg}")
                state.pending_login = None
                state.mark()
                await state.save()
                return
            
            state.pending_login['client'] = client
            state.mark()
            await state.save()
            await self.send(update, f"{msg}\n请输入验证码:")
            return
        
        if state.pending_login['step'] == 'code':
            code = re.sub(r'\s+', '', text)
            if not code.isdigit():
                await self.send(update, "验证码必须为数字")
                return
            
            client = state.pending_login.get('client')
            if not client:
                await self.send(update, "错误: 会话失效，请重新登录")
                state.pending_login = None
                state.mark()
                await state.save()
                return
            
            phone = state.pending_login['phone']
            success, msg = await self.login.complete_login(client, phone, code)
            
            if success:
                if state.client:
                    await state.client.disconnect()
                state.client = client
                state.phone = phone
                state.client_phone = phone
                
                try:
                    me = await client.get_me()
                    state.name = me.first_name or phone
                    state.tg_uid = me.id
                except:
                    state.name = phone
                
                state.pending_login = None
                state.mark()
                await state.save()
                await self.send(update, f"✅ {msg}", self.main_keyboard())
                await self.send(update, self.menu_text(state), self.main_keyboard())
            else:
                if "需要两步验证密码" in msg:
                    state.pending_login['step'] = 'password'
                    state.mark()
                    await state.save()
                    await self.send(update, "🔒 请输入两步验证密码:")
                else:
                    await self.send(update, f"错误: {msg}")
                    state.pending_login = None
                    state.mark()
                    await state.save()
            return
        
        if state.pending_login['step'] == 'password':
            client = state.pending_login.get('client')
            if not client:
                await self.send(update, "错误: 请重新登录")
                state.pending_login = None
                state.mark()
                await state.save()
                return
            
            phone = state.pending_login['phone']
            success, msg = await self.login.complete_login(client, phone, '', text)
            
            if success:
                if state.client:
                    await state.client.disconnect()
                state.client = client
                state.phone = phone
                state.client_phone = phone
                
                try:
                    me = await client.get_me()
                    state.name = me.first_name or phone
                    state.tg_uid = me.id
                except:
                    state.name = phone
                
                state.pending_login = None
                state.mark()
                await state.save()
                await self.send(update, f"✅ {msg}", self.main_keyboard())
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
                history = await self.api.fetch()
                if not history:
                    await asyncio.sleep(30)
                    continue
                
                current = history[0]
                if self._global_period == current.period:
                    await asyncio.sleep(5)
                    continue
                
                self._global_period = current.period
                logger.info(f"新期号: {current.period} {current.nums}={current.sum}")
                
                # 获取所有活跃用户
                states = []
                for uid in list(self.store._cache.keys()):
                    state = await self.store.get(uid)
                    if state.client and state.betting:
                        states.append((uid, state))
                
                # 结算
                for uid, state in states:
                    if state.current_bet and state.current_bet.period == current.period:
                        try:
                            profit = await state.settle(current)
                            if profit != 0:
                                logger.info(f"[{state.name}] 结算 {current.period}: {profit:+.3f}")
                        except Exception as e:
                            logger.error(f"[{state.name}] 结算失败: {e}")
                
                await asyncio.sleep(15)
                
                # 下注
                for uid, state in states:
                    try:
                        if not state.groups:
                            continue
                        
                        # 预测杀组
                        kill = predict_kill(history, state.algorithm)
                        
                        # ===== 记录预测用于胜率统计 =====
                        state.record_prediction(state.algorithm, kill)
                        
                        # 确定下注组（排除杀组）
                        bet_groups = [g for g in Group if g.value != kill]
                        
                        next_period = self.api.next_period(current.period)
                        
                        # 13/14触发
                        triggered = False
                        if state.trigger_enabled and current.sum in (13, 14):
                            state.recommend_amount = int(state.recommend_amount * state.trigger_multiplier)
                            triggered = True
                            logger.info(f"[{state.name}] 13/14触发: {state.recommend_amount}")
                        
                        await asyncio.sleep(state.bet_delay)
                        success = await state.place_bet(bet_groups, next_period, triggered)
                        
                        if success:
                            logger.info(f"[{state.name}] 下注 {next_period}: 杀组={kill}")
                    except Exception as e:
                        logger.error(f"[{state.name}] 下注失败: {e}")
                
                # 汇总
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
            import aiohttp
        except ImportError:
            logger.warning("缺少 aiohttp，正在安装...")
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
            logger.info("aiohttp 已安装")
        
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
        self.app.add_handler(CommandHandler("rank", self.cmd_rank))  # 排行榜命令
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
        
        logger.info("小鶴神机器人已启动")
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
