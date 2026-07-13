#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║         悲天悯人 · PC28 自动投注机器人  v3.0                ║
║         Architecture: Domain-Driven, Modular Monolith        ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio, os, re, json, logging, traceback
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, date
from collections import Counter, defaultdict

import requests
from telethon import TelegramClient, errors as tl_errors
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler,
)
from python_socks import ProxyType

# ═══════════════════════════════════════════════════════════════
# SECTION 0 — 配置中心
# ═══════════════════════════════════════════════════════════════

class Config:
    """集中配置，便于修改与版本控制。"""

    # ── Telegram ──
    API_ID      = 38252668
    API_HASH    = '7bfa9f824e18cd5498b984ee391de2e9'
    BOT_TOKEN   = '8645128854:AAGb-B8S8hIoDf7kyMIZ20_ULYIzcefQkRc'
    OWNER_ID    = 1047239922

    # ── 代理 ──
    PROXIES = [
        dict(server='8.138.35.134', port=443, username='tg_7772150247',
             password='60d382cd54d20dacc972c697de3387d841d9abe8db8cdb89db5916520c2a6a74'),
        dict(server='8.163.67.73',  port=443, username='tg_7772150247',
             password='60d382cd54d20dacc972c697de3387d841d9abe8db8cdb89db5916520c2a6a74'),
    ]

    # ── 默认投注参数 ──
    DEFAULT_BASE_BET       = 60000
    DEFAULT_MARTIN_INCR    = 100000
    DEFAULT_MAX_LOSSES     = 10
    DEFAULT_POLL_INTERVAL  = 30
    DEFAULT_BET_DELAY      = 15
    DEFAULT_TRIGGER_MULT   = 2.0

    # ── 赔率 ──
    ODDS_SMALL_ODD_BIG_EVEN = 4.72
    ODDS_SMALL_EVEN_BIG_ODD = 4.32
    ODDS_SPECIAL_0_27       = 4.72
    ODDS_SPECIAL_BAOZI      = 10.0
    DEFAULT_SPECIAL_AMOUNT  = 10000

    # ── 路径 ──
    DATA_DIR    = "user_data"
    SESSIONS_DIR = "telegram_sessions"

    # ── 外部资源 ──
    WELCOME_IMAGE = "https://free.boltp.com/2026/07/13/6a541b59a069a.webp"
    OFFICIAL_GROUP = "https://t.me/+zt4w3spyTrM2MmFl"
    NOTICE_CHANNEL = "@NXnb677"

    # ── API 端点 ──
    API_PRIMARY = "https://pc28.help/api/kj.json"
    API_FALLBACK = "https://api.api68.com/pks/getLotteryInfo.do?date=&lotCode=10026"

    @classmethod
    def ensure_dirs(cls):
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        os.makedirs(cls.SESSIONS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# SECTION 1 — 日志系统
# ═══════════════════════════════════════════════════════════════

class Log:
    """结构化日志封装，提供层级缩进与颜色标记。"""

    _logger: logging.Logger = None

    @classmethod
    def init(cls):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s │ %(levelname)-7s │ %(message)s',
            datefmt='%m-%d %H:%M:%S',
        )
        cls._logger = logging.getLogger("悲天悯人")

    @classmethod
    def _log(cls, level: int, msg: str):
        if cls._logger is None:
            cls.init()
        cls._logger.log(level, msg)

    @classmethod
    def info(cls, msg: str):   cls._log(logging.INFO, msg)
    @classmethod
    def warn(cls, msg: str):   cls._log(logging.WARNING, msg)
    @classmethod
    def error(cls, msg: str):  cls._log(logging.ERROR, msg)
    @classmethod
    def debug(cls, msg: str):  cls._log(logging.DEBUG, msg)

    @classmethod
    def banner(cls, text: str, width: int = 60):
        cls.info("┌" + "─" * width)
        cls.info(f"│  {text}")
        cls.info("└" + "─" * width)

    @classmethod
    def section(cls, text: str):
        cls.info("")
        cls.info("┌" + "─" * 50)
        cls.info(f"│  {text}")
        cls.info("├" + "─" * 50)

    @classmethod
    def item(cls, text: str):   cls.info(f"  │  {text}")
    @classmethod
    def sub(cls, text: str):    cls.info(f"  ├─ {text}")
    @classmethod
    def end(cls, text: str=""): cls.info(f"  └─ {text}" if text else f"  └─")


# ═══════════════════════════════════════════════════════════════
# SECTION 2 — 领域模型
# ═══════════════════════════════════════════════════════════════

class Group(Enum):
    """四门分组。"""
    SMALL_ODD  = "小单"
    SMALL_EVEN = "小双"
    BIG_ODD    = "大单"
    BIG_EVEN   = "大双"

    @classmethod
    def all_groups(cls) -> List['Group']:
        return list(cls)

    @classmethod
    def opposite(cls, g: 'Group') -> 'Group':
        return {
            cls.SMALL_ODD:  cls.BIG_EVEN,
            cls.BIG_EVEN:   cls.SMALL_ODD,
            cls.SMALL_EVEN: cls.BIG_ODD,
            cls.BIG_ODD:    cls.SMALL_EVEN,
        }[g]


@dataclass(frozen=True)
class Draw:
    """不可变开奖数据。"""
    period:   str
    hundreds: int
    tens:     int
    ones:     int

    @property
    def sum_value(self) -> int:
        return self.hundreds + self.tens + self.ones

    @property
    def group(self) -> Group:
        s = self.sum_value
        return (Group.SMALL_ODD if s <= 13 and s % 2 == 1
                else Group.SMALL_EVEN if s <= 13
                else Group.BIG_ODD if s % 2 == 1
                else Group.BIG_EVEN)

    @property
    def is_baozi(self) -> bool:
        return self.hundreds == self.tens == self.ones

    def __str__(self) -> str:
        return f"{self.hundreds}+{self.tens}+{self.ones}={self.sum_value}"


@dataclass
class BetRecord:
    """单次下注记录。"""
    period:          str
    base_items:      List[Tuple[str, int, float]]  # (名称, 金额, 赔率)
    multipliers:     Dict[int, float]
    trigger_applied: bool = False


class AccuracyTracker:
    """准确率追踪器（滑动窗口）。"""

    def __init__(self, window: int = 50):
        self._records: List[bool] = []
        self._window = window

    def feed(self, predicted_kill: Optional[str], actual: str) -> bool:
        if predicted_kill is None:
            return False
        correct = actual != predicted_kill
        self._records.append(correct)
        if len(self._records) > self._window:
            self._records.pop(0)
        return correct

    def accuracy(self, n: int = 15) -> float:
        recent = self._records[-n:]
        return sum(recent) / len(recent) * 100 if recent else 50.0


# ═══════════════════════════════════════════════════════════════
# SECTION 3 — API 层
# ═══════════════════════════════════════════════════════════════

class DrawAPI:
    """开奖数据获取，主备双 API 容灾。"""

    @staticmethod
    def fetch() -> Optional[Draw]:
        for fetcher in (DrawAPI._fetch_primary, DrawAPI._fetch_fallback):
            result = fetcher()
            if result:
                Log.info(f"[API] 期号:{result.period} 号码:{result}")
                return result
        Log.error("[API] 所有端点均不可用")
        return None

    @staticmethod
    def _fetch_primary() -> Optional[Draw]:
        try:
            r = requests.get(Config.API_PRIMARY, timeout=10)
            if r.status_code != 200:
                return None
            items = r.json().get("data", [])
            if not items:
                return None
            item = items[0]
            digits = [int(d) for d in re.findall(r'\d', str(item.get("number", ""))) if d.isdigit()]
            return Draw(str(item["nbr"]), digits[0], digits[1], digits[2]) if len(digits) >= 3 else None
        except Exception as e:
            Log.warn(f"[API] 主接口异常: {e}")
            return None

    @staticmethod
    def _fetch_fallback() -> Optional[Draw]:
        try:
            r = requests.get(Config.API_FALLBACK, timeout=10)
            if r.status_code != 200:
                return None
            data = r.json()
            last = data.get("result", {}).get("data", [None])[0]
            if not last:
                return None
            nums = (last.get("preDrawCode") or "").split(",")
            if len(nums) >= 3:
                return Draw(str(last["preDrawIssue"]), int(nums[0]), int(nums[1]), int(nums[2]))
        except Exception as e:
            Log.warn(f"[API] 备用接口异常: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
# SECTION 4 — 预测算法
# ═══════════════════════════════════════════════════════════════

class Algorithm(Enum):
    CIT           = "cit"
    BEITIAN_5Y    = "beitian_5y"
    BEITIAN_7Y    = "beitian_7y"
    BEITIAN_HYBRID = "beitian_hybrid"

    @property
    def label(self) -> str:
        return {
            self.CIT:            "CIT杀组",
            self.BEITIAN_5Y:     "悲天悯人5Y",
            self.BEITIAN_7Y:     "悲天悯人7Y",
            self.BEITIAN_HYBRID: "悲天悯人混合",
        }[self]

    @classmethod
    def from_str(cls, s: str) -> 'Algorithm':
        return cls(s)


# ──── CIT 杀组 ────

class CITPredictor:
    """CIT 综合打分杀组算法。"""

    @staticmethod
    def predict(draws: List[Draw]) -> Optional[str]:
        if len(draws) < 5:
            return "小单"
        recent = draws[-10:]
        groups = [d.group.value for d in recent]
        totals = [d.sum_value for d in recent]
        sizes  = ["大" if d.sum_value >= 14 else "小" for d in recent]
        parities = ["单" if d.sum_value % 2 == 1 else "双" for d in recent]
        all_g = ['小单', '小双', '大单', '大双']

        score = {g: 0.0 for g in all_g}

        # 连庄惩罚
        streak = 1
        for i in range(len(groups) - 2, -1, -1):
            if groups[i] == groups[-1]:
                streak += 1
            else:
                break
        if streak >= 3:
            score[groups[-1]] -= streak * 15
        elif streak == 2:
            score[groups[-1]] -= 8

        # 频率
        cnt = Counter(groups)
        for g in all_g:
            score[g] += (10 - cnt.get(g, 0)) * 3

        # 大小交替
        size_streak = 1
        for i in range(len(sizes) - 2, -1, -1):
            if sizes[i] == sizes[-1]:
                size_streak += 1
            else:
                break
        if size_streak >= 3:
            expected = '小' if sizes[-1] == '大' else '大'
            for g in all_g:
                score[g] += 8 if expected in g else -10

        # 奇偶交替
        par_streak = 1
        for i in range(len(parities) - 2, -1, -1):
            if parities[i] == parities[-1]:
                par_streak += 1
            else:
                break
        if par_streak >= 3:
            expected = '双' if parities[-1] == '单' else '单'
            for g in all_g:
                score[g] += 8 if expected in g else -10

        # 趋势
        avg = sum(totals[-5:]) / 5
        for g in all_g:
            if avg > 16:
                score[g] += 5 if '小' in g else -3
            elif avg < 11:
                score[g] += 5 if '大' in g else -3

        # 遗漏
        for g in all_g:
            miss = 0
            for i in range(len(groups) - 1, -1, -1):
                if groups[i] == g:
                    break
                miss += 1
            score[g] += miss * 2

        return min(score, key=score.get)


# ──── 悲天悯人 ────

class BeitianPredictor:
    """悲天悯人系列：5Y / 7Y / 混合。"""

    _YU5_POS  = {0: 'shi', 1: 'ge', 2: 'bai', 3: 'bai_shi', 4: 'ge'}
    _YU7_POS  = {0: 'shi', 1: 'ge', 2: 'bai', 3: 'bai_shi', 4: 'ge', 5: 'shi', 6: 'bai'}
    _YU5_KILL = {0: '小单', 1: '大单', 2: '小双', 3: '大双', 4: '小单'}
    _YU7_KILL_ALERT = {
        0: ('小单', None), 1: ('大单', None), 2: ('小双', None),
        3: ('大双', None), 4: ('小单', None), 5: ('小双', '对子'), 6: ('小单', '顺子'),
    }

    def __init__(self, draws: List[Draw]):
        self._history: List[Dict] = []
        for d in draws:
            self._history.append(dict(
                period=d.period, numbers=str(d), total=d.sum_value,
                combination=d.group.value, is_big=d.sum_value >= 14,
                is_single=d.sum_value % 2 == 1, nums=[d.hundreds, d.tens, d.ones],
                yu5=d.sum_value % 5, yu7=d.sum_value % 7,
            ))

    def predict_5y(self) -> Optional[str]:
        if len(self._history) < 2:
            return None
        cur = self._history[-1]
        refs = [d for d in reversed(self._history[:-1]) if d['yu5'] == cur['yu5']][:4]
        if not refs:
            return None
        kills = []
        for ref in refs:
            new_nums = self._gen_num(cur['nums'], ref['nums'], cur['yu5'], self._YU5_POS)
            kills.append(self._YU5_KILL.get(sum(new_nums) % 5, '小单'))
        return self._vote(kills)

    def predict_7y(self) -> Tuple[Optional[str], Optional[str]]:
        if len(self._history) < 2:
            return None, None
        cur = self._history[-1]
        refs = [d for d in reversed(self._history[:-1]) if d['yu7'] == cur['yu7']][:4]
        if not refs:
            return None, None
        results = []
        for ref in refs:
            new_nums = self._gen_num(cur['nums'], ref['nums'], cur['yu7'], self._YU7_POS)
            kg, alert = self._YU7_KILL_ALERT.get(sum(new_nums) % 7, ('小单', None))
            results.append(dict(kill=kg, alert=alert, ref_period=ref['period']))
        kills = defaultdict(float)
        alerts = []
        for r in results:
            try:
                diff = abs(int(cur['period']) - int(r['ref_period']))
                weight = 1.0 / (1.0 + diff * 0.15)
            except Exception:
                weight = 0.5
            kills[r['kill']] += weight
            if r['alert']:
                alerts.append(r['alert'])
        if not kills:
            return None, None
        best = max(kills, key=kills.get)
        return best, alerts[0] if alerts else None

    def predict_hybrid(self) -> Optional[str]:
        kills = defaultdict(float)
        k5 = self.predict_5y()
        k7, _ = self.predict_7y()
        if k5:
            kills[k5] += 0.5
        if k7:
            kills[k7] += 0.5
        return max(kills, key=kills.get) if kills else None

    @staticmethod
    def _gen_num(cur: List[int], ref: List[int], yu: int, rules: Dict) -> List[int]:
        new = cur.copy()
        rule = rules.get(yu, 'ge')
        if rule == 'bai':
            new[0] = (cur[0] + ref[0]) % 10
        elif rule == 'shi':
            new[1] = (cur[1] + ref[1]) % 10
        elif rule == 'ge':
            new[2] = (cur[2] + ref[2]) % 10
        elif rule == 'bai_shi':
            new[0] = new[1] = (cur[0] + cur[1] + ref[0] + ref[1]) % 10
        return new

    @staticmethod
    def _vote(kills: List[str]) -> Optional[str]:
        if not kills:
            return None
        weights = [1.0, 0.6, 0.4, 0.3, 0.2, 0.1][:len(kills)]
        score = defaultdict(float)
        for k, w in zip(kills, weights):
            score[k] += w
        return max(score, key=score.get)


def predict_kill(draws: List[Draw], algo: Algorithm) -> Optional[str]:
    """统一预测入口。"""
    if algo == Algorithm.CIT:
        return CITPredictor.predict(draws)
    elif algo in (Algorithm.BEITIAN_5Y, Algorithm.BEITIAN_7Y, Algorithm.BEITIAN_HYBRID):
        if len(draws) < 2:
            return None
        bp = BeitianPredictor(draws)
        if algo == Algorithm.BEITIAN_5Y:
            return bp.predict_5y()
        elif algo == Algorithm.BEITIAN_7Y:
            k, _ = bp.predict_7y()
            return k
        else:
            return bp.predict_hybrid()
    return None


# ═══════════════════════════════════════════════════════════════
# SECTION 5 — 用户状态
# ═══════════════════════════════════════════════════════════════

class UserState:
    """每个用户的完整状态，支持 JSON 持久化。"""

    __slots__ = (
        'user_id', 'target_group_ids', 'group_multipliers', 'default_group_id',
        'balance', 'last_balance', 'usdt_balance', 'cny_balance', 'kkcoin_balance',
        'profit_target', 'loss_target', 'daily_profit', 'last_date',
        'base_bet_amount', 'martin_increment', 'max_consecutive_losses',
        'betting_enabled', 'consecutive_losses', 'current_recommend_amount',
        'last_processed_period', 'current_bet', 'history',
        'special_0_enabled', 'special_0_amount', 'special_27_enabled', 'special_27_amount',
        'special_baozi_enabled', 'special_baozi_amount',
        'telegram_client', 'client_phone', 'account_name', 'telegram_user_id',
        'pending_login', 'pending_action',
        'bet_delay_seconds', 'poll_interval',
        'trigger_enabled', 'trigger_multiplier',
        'algorithm', 'tracker', 'prediction_log', 'balance_query_group_id',
    )

    def __init__(self, user_id: int):
        self.user_id               = user_id
        self.target_group_ids: List[int] = []
        self.group_multipliers: Dict[int, float] = {}
        self.default_group_id: Optional[int] = None
        # 余额
        self.balance       = 0.0
        self.last_balance  = 0.0
        self.usdt_balance  = 0.0
        self.cny_balance   = 0.0
        self.kkcoin_balance = 0.0
        # 风控
        self.profit_target = 0.0
        self.loss_target   = 0.0
        self.daily_profit  = 0.0
        self.last_date     = date.today().isoformat()
        # 马丁格尔
        self.base_bet_amount         = Config.DEFAULT_BASE_BET
        self.martin_increment        = Config.DEFAULT_MARTIN_INCR
        self.max_consecutive_losses  = Config.DEFAULT_MAX_LOSSES
        self.betting_enabled         = True
        self.consecutive_losses      = 0
        self.current_recommend_amount = Config.DEFAULT_BASE_BET
        # 执行状态
        self.last_processed_period: Optional[str] = None
        self.current_bet: Optional[BetRecord] = None
        self.history: List[Draw] = []
        # 特殊项
        self.special_0_enabled    = True
        self.special_0_amount     = Config.DEFAULT_SPECIAL_AMOUNT
        self.special_27_enabled   = True
        self.special_27_amount    = Config.DEFAULT_SPECIAL_AMOUNT
        self.special_baozi_enabled = True
        self.special_baozi_amount  = Config.DEFAULT_SPECIAL_AMOUNT
        # Telegram 客户端
        self.telegram_client: Optional[TelegramClient] = None
        self.client_phone:     Optional[str] = None
        self.account_name      = "未登录"
        self.telegram_user_id: Optional[int] = None
        # 交互状态
        self.pending_login:  Optional[dict] = None
        self.pending_action: Optional[str]  = None
        # 参数
        self.bet_delay_seconds  = Config.DEFAULT_BET_DELAY
        self.poll_interval      = Config.DEFAULT_POLL_INTERVAL
        self.trigger_enabled    = True
        self.trigger_multiplier = Config.DEFAULT_TRIGGER_MULT
        self.algorithm          = "cit"
        self.tracker            = AccuracyTracker()
        self.prediction_log: List[Dict] = []
        self.balance_query_group_id: Optional[int] = None

    # ── 持久化 ──

    def _path(self) -> str:
        return os.path.join(Config.DATA_DIR, f"user_{self.user_id}.json")

    def save(self):
        try:
            with open(self._path(), 'w') as f:
                json.dump(self._serialize(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            Log.error(f"[保存失败] 用户{self.user_id}: {e}")

    def load(self):
        if not os.path.exists(self._path()):
            return
        try:
            with open(self._path()) as f:
                d = json.load(f)
            self._deserialize(d)
        except Exception as e:
            Log.error(f"[加载失败] 用户{self.user_id}: {e}")

    def _serialize(self) -> dict:
        return dict(
            user_id=self.user_id, target_group_ids=self.target_group_ids,
            group_multipliers=self.group_multipliers, default_group_id=self.default_group_id,
            balance=self.balance, last_balance=self.last_balance,
            usdt_balance=self.usdt_balance, cny_balance=self.cny_balance,
            kkcoin_balance=self.kkcoin_balance,
            profit_target=self.profit_target, loss_target=self.loss_target,
            daily_profit=self.daily_profit, last_date=self.last_date,
            base_bet_amount=self.base_bet_amount, martin_increment=self.martin_increment,
            max_consecutive_losses=self.max_consecutive_losses,
            betting_enabled=self.betting_enabled, consecutive_losses=self.consecutive_losses,
            current_recommend_amount=self.current_recommend_amount,
            last_processed_period=self.last_processed_period,
            account_name=self.account_name, client_phone=self.client_phone,
            telegram_user_id=self.telegram_user_id,
            history=[dict(period=d.period, hundreds=d.hundreds, tens=d.tens, ones=d.ones) for d in self.history],
            bet_delay_seconds=self.bet_delay_seconds, poll_interval=self.poll_interval,
            special_0_enabled=self.special_0_enabled, special_0_amount=self.special_0_amount,
            special_27_enabled=self.special_27_enabled, special_27_amount=self.special_27_amount,
            special_baozi_enabled=self.special_baozi_enabled, special_baozi_amount=self.special_baozi_amount,
            trigger_enabled=self.trigger_enabled, trigger_multiplier=self.trigger_multiplier,
            algorithm=self.algorithm, prediction_log=self.prediction_log,
            balance_query_group_id=self.balance_query_group_id,
        )

    def _deserialize(self, d: dict):
        for k in ('target_group_ids', 'balance', 'last_balance', 'usdt_balance', 'cny_balance',
                  'kkcoin_balance', 'profit_target', 'loss_target', 'daily_profit', 'last_date',
                  'base_bet_amount', 'martin_increment', 'max_consecutive_losses',
                  'betting_enabled', 'consecutive_losses', 'current_recommend_amount',
                  'last_processed_period', 'account_name', 'client_phone', 'telegram_user_id',
                  'bet_delay_seconds', 'poll_interval', 'special_0_enabled', 'special_0_amount',
                  'special_27_enabled', 'special_27_amount', 'special_baozi_enabled',
                  'special_baozi_amount', 'trigger_enabled', 'trigger_multiplier',
                  'algorithm', 'prediction_log', 'balance_query_group_id',
                  'group_multipliers', 'default_group_id'):
            if k in d:
                setattr(self, k, d[k])
        # 兼容旧版单群组
        if not self.target_group_ids and d.get("target_group_id"):
            self.target_group_ids = [d["target_group_id"]]
        # 恢复历史
        self.history = [Draw(h["period"], h["hundreds"], h["tens"], h["ones"]) for h in d.get("history", [])]

    # ── 业务方法 ──

    @property
    def algo(self) -> Algorithm:
        return Algorithm.from_str(self.algorithm)

    def add_profit(self, amount: float):
        today = date.today().isoformat()
        if self.last_date != today:
            self.daily_profit = 0.0
            self.last_date = today
        self.daily_profit += amount
        self.balance += amount
        self.save()

    def odds(self, g: Group) -> float:
        return Config.ODDS_SMALL_ODD_BIG_EVEN if g in (Group.SMALL_ODD, Group.BIG_EVEN) else Config.ODDS_SMALL_EVEN_BIG_ODD

    def special_items(self) -> List[Tuple[str, float, int]]:
        items = []
        if self.special_0_enabled:
            items.append(("0", Config.ODDS_SPECIAL_0_27, self.special_0_amount))
        if self.special_27_enabled:
            items.append(("27", Config.ODDS_SPECIAL_0_27, self.special_27_amount))
        if self.special_baozi_enabled:
            items.append(("豹子", Config.ODDS_SPECIAL_BAOZI, self.special_baozi_amount))
        return items

    def is_win(self, name: str, draw: Draw) -> bool:
        if name in ('小单', '小双', '大单', '大双'):
            return draw.group.value == name
        if name == '0':
            return draw.sum_value == 0
        if name == '27':
            return draw.sum_value == 27
        if name == '豹子':
            return draw.is_baozi
        return False

    async def settle(self, draw: Draw) -> float:
        if not self.current_bet:
            return 0.0
        bet = self.current_bet
        total = 0.0
        recommend_profit = 0.0

        for name, base_amt, odds in bet.base_items:
            if name in ('小单', '小双', '大单', '大双'):
                recommend_profit += (base_amt * odds - base_amt) if self.is_win(name, draw) else -base_amt

        for gid, mult in bet.multipliers.items():
            for name, base_amt, odds in bet.base_items:
                amt = int(base_amt * mult)
                total += (amt * odds - amt) if self.is_win(name, draw) else -amt

        self.add_profit(total)

        if bet.trigger_applied:
            self.current_recommend_amount = self.base_bet_amount
            self.consecutive_losses = 0
        elif recommend_profit > 0:
            self.current_recommend_amount = self.base_bet_amount
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.current_recommend_amount += self.martin_increment
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.current_recommend_amount = self.base_bet_amount
                self.consecutive_losses = 0

        self.current_bet = None
        self.save()
        return total

    async def place_bet(self, next_period: str, groups: List[Group], trigger: bool = False) -> bool:
        if self.consecutive_losses >= self.max_consecutive_losses and not trigger:
            return False
        if not self.telegram_client or not self.target_group_ids:
            return False

        base_items = [(g.value, self.current_recommend_amount, self.odds(g)) for g in groups]
        base_items += [(n, a, o) for n, o, a in self.special_items()]
        if not base_items:
            return False

        mult_snapshot = {gid: self.group_multipliers.get(gid, 1.0) for gid in self.target_group_ids}

        for gid in self.target_group_ids:
            mult = mult_snapshot.get(gid, 1.0)
            parts = [f"{name} {int(base_amt * mult)}" for name, base_amt, _ in base_items if int(base_amt * mult) > 0]
            if parts:
                try:
                    await self.telegram_client.send_message(gid, " ".join(parts))
                except Exception as e:
                    Log.error(f"[下发失败] {self.account_name}(ID:{self.user_id}) → 群{gid}: {e}")

        self.current_bet = BetRecord(period=next_period, base_items=base_items,
                                      multipliers=mult_snapshot, trigger_applied=trigger)
        self.save()
        return True


# ═══════════════════════════════════════════════════════════════
# SECTION 6 — 键盘工厂
# ═══════════════════════════════════════════════════════════════

class Keyboard:
    """统一键盘定义。"""

    MAIN = ReplyKeyboardMarkup([
        ["启动挂机",    "停止挂机"],
        ["今日输赢",    "账号状态"],
        ["登录账号",    "登出账号"],
        ["群组管理",    "切换算法"],
        ["下注设置",    "特殊项设置"],
        ["13/14设置"],
        ["会话列表"],
    ], resize_keyboard=True)

    GROUP = ReplyKeyboardMarkup([
        ["添加群组",    "查看群组"],
        ["设置倍数",    "清空群组"],
        ["设置默认群组", "返回主菜单"],
    ], resize_keyboard=True)

    BET = ReplyKeyboardMarkup([
        ["设置起步金额",  "设置递增金额"],
        ["设置最大连输",  "设置止盈目标"],
        ["设置止损目标",  "设置下注延迟"],
        ["设置轮询间隔",  "重置今日盈亏"],
        ["返回主菜单"],
    ], resize_keyboard=True)

    SPECIAL = ReplyKeyboardMarkup([
        ["0 开关/金额",   "27 开关/金额"],
        ["豹子 开关/金额"],
        ["返回主菜单"],
    ], resize_keyboard=True)

    SPECIAL_SUB = ReplyKeyboardMarkup([
        ["开启", "关闭"],
        ["返回特殊项设置"],
    ], resize_keyboard=True)

    TRIGGER = ReplyKeyboardMarkup([
        ["开启13/14", "关闭13/14"],
        ["设置触发倍数"],
        ["返回主菜单"],
    ], resize_keyboard=True)

    METHOD = ReplyKeyboardMarkup([
        ["短信验证码", "语音验证码"],
    ], resize_keyboard=True)

    ALGO = ReplyKeyboardMarkup([
        ["CIT杀组算法",   "悲天悯人5Y"],
        ["悲天悯人7Y",   "悲天悯人混合"],
        ["返回主菜单"],
    ], resize_keyboard=True)

    @staticmethod
    def admin_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 总体概况", callback_data="admin_overview")],
            [InlineKeyboardButton("💰 按KKCOIN排序", callback_data="admin_sort_balance"),
             InlineKeyboardButton("📈 按今日盈亏排序", callback_data="admin_sort_daily")],
            [InlineKeyboardButton("✅ 挂机中用户", callback_data="admin_filter_running"),
             InlineKeyboardButton("🔴 未挂机用户", callback_data="admin_filter_stopped")],
            [InlineKeyboardButton("📋 按USDT排序", callback_data="admin_sort_usdt"),
             InlineKeyboardButton("📋 按CNY排序", callback_data="admin_sort_cny")],
            [InlineKeyboardButton("🔍 查看指定用户", callback_data="admin_user_prompt")],
            [InlineKeyboardButton("🔄 刷新数据", callback_data="admin_refresh")],
        ])

    @staticmethod
    def admin_back() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 返回主菜单", callback_data="admin_menu")],
        ])


# ═══════════════════════════════════════════════════════════════
# SECTION 7 — 主机器人
# ═══════════════════════════════════════════════════════════════

class Bot:
    """悲天悯人自动投注主控类。"""

    def __init__(self):
        self._users: Dict[int, UserState] = {}
        self._running = True
        self._global_last_period: Optional[str] = None
        self._app: Optional[Application] = None
        self._bot = None
        self._msg_log: Dict[int, List[str]] = defaultdict(list)

    # ── 工具 ──

    def _user(self, uid: int) -> UserState:
        if uid not in self._users:
            self._users[uid] = UserState(uid)
            self._users[uid].load()
        return self._users[uid]

    async def _reply(self, update: Update, text: str, markup=None):
        try:
            await update.message.reply_text(text, reply_markup=markup)
        except Exception as e:
            Log.error(f"[回复失败] {e}")

    async def _dm(self, uid: int, text: str):
        if not self._bot:
            return
        try:
            await self._bot.send_message(uid, text)
            ts = datetime.now().strftime('%m-%d %H:%M')
            self._msg_log[uid].append(f"{ts} │ {text[:200]}")
            if len(self._msg_log[uid]) > 100:
                self._msg_log[uid] = self._msg_log[uid][-100:]
        except Exception as e:
            Log.error(f"[私信失败] 用户{uid}: {e}")

    async def _refresh_name(self, uid: int):
        state = self._user(uid)
        if state.telegram_client:
            try:
                me = await state.telegram_client.get_me()
                state.account_name = me.first_name or state.client_phone or "未知"
                state.telegram_user_id = me.id
            except Exception:
                state.account_name = "已登录(获取失败)"
        else:
            state.account_name = "未登录"
        state.save()

    async def _resolve_group(self, state: UserState, raw: str) -> Optional[int]:
        if not state.telegram_client:
            return None
        raw = raw.strip()
        if re.match(r'^-?\d+$', raw):
            return int(raw)
        m = re.search(r'(?:t\.me/|@)([a-zA-Z0-9_]+)', raw)
        if m:
            try:
                return (await state.telegram_client.get_entity(f"@{m.group(1)}")).id
            except Exception:
                return None
        try:
            return (await state.telegram_client.get_entity(raw)).id
        except Exception:
            return None

    # ── 菜单渲染 ──

    def _render_menu(self, state: UserState) -> str:
        status = "🟢 挂机中" if state.betting_enabled else "🔴 已停止"
        trigger = "✅ 开启" if state.trigger_enabled else "❌ 关闭"
        groups = "\n".join(
            f"  • {gid} (×{state.group_multipliers.get(gid, 1.0):.2f})"
            for gid in state.target_group_ids
        ) if state.target_group_ids else "  (未设置)"

        return (
            f"╔══════════════════════════════════╗\n"
            f"║    悲天悯人 · 自动投注           ║\n"
            f"╚══════════════════════════════════╝\n\n"
            f"👤 {state.account_name}\n"
            f"▶️  状态: {status}    ⚡ 13/14: {trigger}\n\n"
            f"┌── 余额 ──────────────\n"
            f"│ 💵 USDT : {state.usdt_balance:.3f}\n"
            f"│ 💴 CNY  : {state.cny_balance:.3f}\n"
            f"│ 💎 KKCOIN: {state.kkcoin_balance:.3f}\n"
            f"└──────────────────────\n\n"
            f"🎯 起步: {state.base_bet_amount}   递增: {state.martin_increment}\n"
            f"📈 止盈: {state.profit_target:.0f}    📉 止损: {state.loss_target:.0f}\n\n"
            f"🧠 算法: {state.algo.label}\n"
            f"📢 群组:\n{groups}\n\n"
            f"✈ 交流群: {Config.OFFICIAL_GROUP}\n"
            f"📣 通知: {Config.NOTICE_CHANNEL}"
        )

    # ── 命令处理器 ──

    async def cmd_start(self, update: Update, _):
        state = self._user(update.effective_chat.id)
        state.pending_action = None
        state.save()
        try:
            await update.message.reply_photo(
                photo=Config.WELCOME_IMAGE,
                caption="🎉 欢迎使用 悲天悯人 自动投注\n自动下注 · 智能预测 · 盈亏追踪"
            )
        except Exception:
            pass
        await self._reply(update, self._render_menu(state), Keyboard.MAIN)

    async def cmd_go(self, update: Update, _):
        state = self._user(update.effective_chat.id)
        if not state.target_group_ids and state.default_group_id:
            state.target_group_ids.append(state.default_group_id)
            state.group_multipliers[state.default_group_id] = 1.0
            state.save()
        if not state.target_group_ids:
            return await self._reply(update, "❌ 请先添加下注群组", Keyboard.MAIN)
        if not state.telegram_client:
            return await self._reply(update, "❌ 请先登录下注账号", Keyboard.MAIN)
        state.betting_enabled = True
        state.save()
        await self._reply(update, f"✅ 挂机已启动  |  KKCOIN: {state.kkcoin_balance:.3f}", Keyboard.MAIN)

    async def cmd_stop(self, update: Update, _):
        state = self._user(update.effective_chat.id)
        state.betting_enabled = False
        state.save()
        await self._reply(update, "⏸️ 挂机已停止", Keyboard.MAIN)

    async def cmd_profit(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        await self._reply(update, (
            f"━━━ 📈 今日盈亏 ━━━\n\n"
            f"💰 今日: {s.daily_profit:+,.3f} KKCOIN\n"
            f"💵 USDT: {s.usdt_balance:.3f}\n"
            f"💴 CNY:  {s.cny_balance:.3f}\n"
            f"💎 KKCOIN: {s.kkcoin_balance:.3f}"
        ), Keyboard.MAIN)

    async def cmd_status(self, update: Update, _):
        uid = update.effective_chat.id
        await self._refresh_name(uid)
        s = self._user(uid)
        await self._reply(update, (
            f"━━━ 👤 账号状态 ━━━\n\n"
            f"👤 {s.account_name}\n"
            f"📱 {s.client_phone or '未登录'}\n\n"
            f"💎 KKCOIN: {s.kkcoin_balance:.3f}\n"
            f"💵 USDT: {s.usdt_balance:.3f}\n"
            f"💴 CNY:  {s.cny_balance:.3f}\n\n"
            f"🧠 {s.algo.label}\n"
            f"▶️  {'挂机中' if s.betting_enabled else '已停止'}\n"
            f"⚡ 13/14: {'开启' if s.trigger_enabled else '关闭'} ×{s.trigger_multiplier:.2f}"
        ), Keyboard.MAIN)

    # ── 群组管理 ──

    async def cmd_add_group(self, update: Update, ctx):
        s = self._user(update.effective_chat.id)
        if not s.telegram_client:
            return await self._reply(update, "❌ 请先登录")
        if not ctx.args:
            return await self._reply(update, "❌ /add_group <群ID/链接/@用户名>")
        gid = await self._resolve_group(s, ctx.args[0])
        if gid is None:
            return await self._reply(update, "❌ 无法解析群组")
        if gid in s.target_group_ids:
            return await self._reply(update, f"❌ 群组 {gid} 已存在")
        s.target_group_ids.append(gid)
        s.group_multipliers[gid] = 1.0
        s.save()
        await self._reply(update, f"✅ 已添加群组 {gid} (×1.00)", Keyboard.GROUP)

    async def cmd_set_default_group(self, update: Update, ctx):
        s = self._user(update.effective_chat.id)
        if not ctx.args:
            return await self._reply(update, "❌ /set_default_group <群ID/链接>")
        if not s.telegram_client:
            return await self._reply(update, "❌ 请先登录")
        gid = await self._resolve_group(s, ctx.args[0])
        if gid is None:
            return await self._reply(update, "❌ 无法解析群组")
        s.default_group_id = gid
        if gid not in s.target_group_ids:
            s.target_group_ids.append(gid)
            s.group_multipliers[gid] = 1.0
        s.save()
        await self._reply(update, f"✅ 默认群组 → {gid}", Keyboard.GROUP)

    async def cmd_list_groups(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        if not s.target_group_ids:
            return await self._reply(update, "📋 暂无群组", Keyboard.GROUP)
        lines = ["📋 下注群组:"]
        for gid in s.target_group_ids:
            lines.append(f"  • {gid} (×{s.group_multipliers.get(gid, 1.0):.2f})")
        if s.default_group_id:
            lines.append(f"⭐ 默认: {s.default_group_id}")
        await self._reply(update, "\n".join(lines), Keyboard.GROUP)

    async def cmd_clear_groups(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        s.target_group_ids = []
        s.group_multipliers = {}
        s.save()
        await self._reply(update, "✅ 群组已清空", Keyboard.GROUP)

    async def cmd_set_multiplier(self, update: Update, ctx):
        if len(ctx.args) != 2:
            return await self._reply(update, "❌ /set_multiplier <群ID> <倍数>")
        try:
            gid, mult = int(ctx.args[0]), float(ctx.args[1])
            if mult <= 0:
                return await self._reply(update, "❌ 倍数必须 > 0")
            s = self._user(update.effective_chat.id)
            if gid not in s.target_group_ids:
                return await self._reply(update, f"❌ 群组 {gid} 不在列表中")
            s.group_multipliers[gid] = mult
            s.save()
            await self._reply(update, f"✅ 群组 {gid} → ×{mult:.2f}", Keyboard.GROUP)
        except ValueError:
            await self._reply(update, "❌ 格式错误")

    # ── 登录 ──

    async def cmd_login(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        s.pending_login = {"step": "waiting_phone"}
        s.pending_action = None
        s.save()
        await self._reply(update, "📱 请输入手机号 (+861234567890):")

    async def cmd_logout(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        if s.telegram_client:
            try:
                await s.telegram_client.disconnect()
            except Exception:
                pass
            s.telegram_client = None
            s.client_phone = None
            s.account_name = "未登录"
            s.telegram_user_id = None
            s.save()
        await self._reply(update, "✅ 已登出", Keyboard.MAIN)

    async def cmd_sessions(self, update: Update, _):
        files = [f for f in os.listdir(Config.SESSIONS_DIR) if f.endswith('.session')]
        await self._reply(update, f"📁 会话: {', '.join(files) if files else '无'}", Keyboard.MAIN)

    def _session_path(self, phone: str) -> str:
        return os.path.join(Config.SESSIONS_DIR, f"{phone.replace('+', 'plus').replace(' ', '')}.session")

    async def _telegram_login_send(self, phone: str, voice: bool) -> Tuple[bool, str, Optional[TelegramClient]]:
        path = self._session_path(phone)
        for proxy in Config.PROXIES:
            try:
                client = TelegramClient(
                    path, Config.API_ID, Config.API_HASH,
                    proxy=(ProxyType.SOCKS5, proxy['server'], proxy['port'],
                           True, proxy['username'], proxy['password']),
                    connection_retries=3, retry_delay=2,
                )
                await client.connect()
                await client.send_code_request(phone)
                return True, "验证码已发送", client
            except tl_errors.FloodWaitError as e:
                return False, f"请等待 {e.seconds} 秒", None
            except Exception as e:
                Log.warn(f"[代理] {e}")
                continue
        return False, "所有代理失败", None

    async def _telegram_login_finish(self, client: TelegramClient, phone: str,
                                      code: str, password: str = "") -> Tuple[bool, str]:
        try:
            await client.sign_in(phone, code)
            return True, "登录成功"
        except tl_errors.SessionPasswordNeededError:
            if password:
                try:
                    await client.sign_in(password=password)
                    return True, "登录成功"
                except Exception as e:
                    return False, f"密码错误: {e}"
            return False, "需要两步验证密码"
        except tl_errors.PhoneCodeInvalidError:
            return False, "验证码错误"
        except tl_errors.PhoneCodeExpiredError:
            return False, "验证码已过期"
        except Exception as e:
            return False, str(e)

    async def _handle_login(self, update: Update, text: str):
        uid = update.effective_chat.id
        s = self._user(uid)
        if not s.pending_login:
            return

        step = s.pending_login["step"]

        if step == "waiting_phone":
            if not re.match(r'^\+\d{7,15}$', text):
                return await self._reply(update, "❌ 手机号格式错误 (+861234567890)")
            s.pending_login["phone"] = text
            s.pending_login["step"] = "waiting_method"
            s.save()
            await self._reply(update, "选择验证方式:", Keyboard.METHOD)

        elif step == "waiting_method":
            if text in ("短信验证码", "1"):
                voice = False
            elif text in ("语音验证码", "2"):
                voice = True
            else:
                return await self._reply(update, "请点击按钮", Keyboard.METHOD)
            ok, msg, client = await self._telegram_login_send(s.pending_login["phone"], voice)
            if not ok:
                s.pending_login = None
                s.save()
                return await self._reply(update, f"❌ {msg}")
            s.pending_login["client"] = client
            s.pending_login["step"] = "waiting_code"
            s.save()
            await self._reply(update, "📨 验证码已发送，请输入:")

        elif step == "waiting_code":
            code = re.sub(r'\s+', '', text)
            if not code.isdigit():
                return await self._reply(update, "❌ 验证码必须为数字")
            client = s.pending_login.get("client")
            if not client:
                s.pending_login = None
                s.save()
                return await self._reply(update, "❌ 会话失效，请重新登录")
            ok, msg = await self._telegram_login_finish(client, s.pending_login["phone"], code)
            if ok:
                if s.telegram_client:
                    await s.telegram_client.disconnect()
                s.telegram_client = client
                s.client_phone = s.pending_login["phone"]
                await self._refresh_name(uid)
                await self._reply(update, f"✅ 登录成功: {s.account_name}", Keyboard.MAIN)
                s.pending_login = None
                s.save()
            elif msg == "需要两步验证密码":
                s.pending_login["step"] = "waiting_password"
                s.save()
                await self._reply(update, "🔐 请输入两步验证密码:")
            else:
                await self._reply(update, f"❌ {msg}")
                s.pending_login = None
                s.save()

        elif step == "waiting_password":
            client = s.pending_login.get("client")
            if not client:
                s.pending_login = None
                s.save()
                return await self._reply(update, "❌ 请重新登录")
            ok, msg = await self._telegram_login_finish(client, s.pending_login["phone"], "", text)
            if ok:
                if s.telegram_client:
                    await s.telegram_client.disconnect()
                s.telegram_client = client
                s.client_phone = s.pending_login["phone"]
                await self._refresh_name(uid)
                await self._reply(update, f"✅ 登录成功: {s.account_name}", Keyboard.MAIN)
            else:
                await self._reply(update, f"❌ {msg}")
            s.pending_login = None
            s.save()

    # ── 设置 ──

    async def cmd_set_balance(self, update: Update, ctx):
        try:
            v = float(ctx.args[0])
            s = self._user(update.effective_chat.id)
            s.balance = s.kkcoin_balance = s.last_balance = v
            s.save()
            await self._reply(update, f"✅ 余额 → {v:.3f} KKCOIN", Keyboard.MAIN)
        except Exception:
            await self._reply(update, "❌ /set_balance <金额>")

    async def cmd_set_profit_target(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_profit_target")

    async def cmd_set_loss_target(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_loss_target")

    async def cmd_set_base(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_base")

    async def cmd_set_increment(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_increment")

    async def cmd_set_max_losses(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_max_losses")

    async def cmd_set_bet_delay(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_bet_delay")

    async def cmd_set_poll(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "set_poll")

    async def cmd_trigger_multiplier(self, update: Update, ctx):
        await self._handle_numeric_cmd(update, ctx, "trigger_multiplier")

    async def _handle_numeric_cmd(self, update: Update, ctx, action: str):
        if not ctx.args:
            return await self._reply(update, f"❌ /{action} <数值>")
        await self._handle_numeric_setting(update, action, ctx.args[0])

    _NUMERIC_SETTINGS = {
        "set_base":             ("起步金额",   "base_bet_amount",         int,   "current_recommend_amount"),
        "set_increment":        ("递增金额",   "martin_increment",        int,   None),
        "set_max_losses":       ("最大连输",   "max_consecutive_losses",  int,   None),
        "set_profit_target":    ("止盈目标",   "profit_target",           float, None),
        "set_loss_target":      ("止损目标",   "loss_target",             float, None),
        "set_bet_delay":        ("下注延迟",   "bet_delay_seconds",       int,   None),
        "set_poll":             ("轮询间隔",   "poll_interval",           int,   None),
        "trigger_multiplier":   ("触发倍数",   "trigger_multiplier",      float, None),
    }

    async def _handle_numeric_setting(self, update: Update, action: str, text: str):
        s = self._user(update.effective_chat.id)
        label, attr, cast, sync = self._NUMERIC_SETTINGS[action]
        try:
            val = cast(text)
            if val < 0:
                raise ValueError
            if action == "trigger_multiplier" and val <= 0:
                raise ValueError
            setattr(s, attr, val)
            if sync:
                setattr(s, sync, val)
            s.save()
            unit = " KKCOIN" if action in ("set_profit_target", "set_loss_target") else ""
            if action == "trigger_multiplier":
                await self._reply(update, f"✅ {label} → {val:.2f}", Keyboard.TRIGGER)
            elif action in ("set_profit_target", "set_loss_target"):
                await self._reply(update, f"✅ {label} → {val:.2f}{unit}", Keyboard.BET)
            elif action in ("set_bet_delay", "set_poll"):
                await self._reply(update, f"✅ {label} → {val} 秒", Keyboard.BET)
            else:
                await self._reply(update, f"✅ {label} → {val}", Keyboard.BET)
        except ValueError:
            await self._reply(update, "❌ 输入无效")

    async def cmd_reset_daily(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        s.daily_profit = 0.0
        s.last_date = date.today().isoformat()
        s.save()
        await self._reply(update, "🔄 今日盈亏已重置", Keyboard.BET)

    # ── 特殊项 ──

    _SPECIAL_MAP = {
        "special_0":  ("special_0_enabled",  "special_0_amount"),
        "special_27": ("special_27_enabled", "special_27_amount"),
        "special_baozi": ("special_baozi_enabled", "special_baozi_amount"),
    }

    async def cmd_special_show(self, update: Update, _):
        s = self._user(update.effective_chat.id)
        await self._reply(update, (
            f"━━━ 特殊项设置 ━━━\n\n"
            f"0:   {'✅' if s.special_0_enabled else '❌'} | {s.special_0_amount} KK\n"
            f"27:  {'✅' if s.special_27_enabled else '❌'} | {s.special_27_amount} KK\n"
            f"豹子: {'✅' if s.special_baozi_enabled else '❌'} | {s.special_baozi_amount} KK"
        ), Keyboard.SPECIAL)

    async def cmd_special_0(self, update: Update, ctx):
        await self._handle_special(update, ctx, "special_0")

    async def cmd_special_27(self, update: Update, ctx):
        await self._handle_special(update, ctx, "special_27")

    async def cmd_special_baozi(self, update: Update, ctx):
        await self._handle_special(update, ctx, "special_baozi")

    async def _handle_special(self, update: Update, ctx, key: str):
        s = self._user(update.effective_chat.id)
        if not ctx.args:
            return await self._reply(update, f"❌ /{key} <on|off|金额>")
        arg = ctx.args[0].strip().lower()
        en_attr, amt_attr = self._SPECIAL_MAP[key]
        if arg in ("on", "off"):
            setattr(s, en_attr, arg == "on")
            s.save()
            await self._reply(update, f"✅ {'启用' if arg == 'on' else '关闭'}", Keyboard.SPECIAL)
        else:
            try:
                amt = int(arg)
                if amt < 0:
                    raise ValueError
                setattr(s, amt_attr, amt)
                s.save()
                await self._reply(update, f"✅ 金额 → {amt} KK", Keyboard.SPECIAL)
            except ValueError:
                await self._reply(update, "❌ 金额必须为正整数")

    # ── 13/14 ──

    async def cmd_trigger(self, update: Update, ctx):
        s = self._user(update.effective_chat.id)
        if not ctx.args:
            return await self._reply(update, (
                f"⚡ 13/14倍投: {'开启' if s.trigger_enabled else '关闭'}\n"
                f"   倍数: ×{s.trigger_multiplier:.2f}"
            ), Keyboard.TRIGGER)
        arg = ctx.args[0].lower()
        if arg == "on":
            s.trigger_enabled = True
        elif arg == "off":
            s.trigger_enabled = False
        else:
            return await self._reply(update, "❌ /trigger on|off")
        s.save()
        await self._reply(update, f"✅ 13/14倍投 → {'开启' if s.trigger_enabled else '关闭'}", Keyboard.TRIGGER)

    # ── 算法切换 ──

    async def _switch_algo(self, update: Update, algo: Algorithm):
        s = self._user(update.effective_chat.id)
        s.algorithm = algo.value
        s.save()
        await self._reply(update, f"✅ 算法 → {algo.label}", Keyboard.MAIN)

    # ── 中文按钮路由 ──

    async def _route_button(self, update: Update, text: str):
        uid = update.effective_chat.id
        s = self._user(uid)

        # 登录流程拦截
        if s.pending_login:
            return await self._handle_login(update, text)

        # 待处理操作
        if s.pending_action:
            return await self._resolve_pending(update, text)

        # 主菜单
        route = {
            "启动挂机":    self.cmd_go,
            "停止挂机":    self.cmd_stop,
            "今日输赢":    self.cmd_profit,
            "账号状态":    self.cmd_status,
            "登录账号":    self.cmd_login,
            "登出账号":    self.cmd_logout,
            "会话列表":    self.cmd_sessions,
            "返回主菜单":  self.cmd_start,
        }
        if text in route:
            return await route[text](update, None)

        # 子菜单导航
        nav = {
            "群组管理": Keyboard.GROUP,
            "下注设置": Keyboard.BET,
            "特殊项设置": Keyboard.SPECIAL,
            "13/14设置": Keyboard.TRIGGER,
            "切换算法": Keyboard.ALGO,
        }
        if text in nav:
            return await self._reply(update, f"📁 {text}", nav[text])

        # 算法
        algo_map = {
            "CIT杀组算法":    Algorithm.CIT,
            "悲天悯人5Y":    Algorithm.BEITIAN_5Y,
            "悲天悯人7Y":    Algorithm.BEITIAN_7Y,
            "悲天悯人混合":  Algorithm.BEITIAN_HYBRID,
        }
        if text in algo_map:
            return await self._switch_algo(update, algo_map[text])

        # 群组管理
        if text == "添加群组":
            s.pending_action = "add_group"; s.save()
            return await self._reply(update, "请输入群组 ID / @用户名 / 链接:")
        if text == "查看群组":
            return await self.cmd_list_groups(update, None)
        if text == "清空群组":
            return await self.cmd_clear_groups(update, None)
        if text == "设置倍数":
            s.pending_action = "set_multiplier"; s.save()
            return await self._reply(update, "格式: 群ID 倍数\n例: -100123456789 2.0")
        if text == "设置默认群组":
            s.pending_action = "set_default_group"; s.save()
            return await self._reply(update, "请输入群组 ID / @用户名 / 链接:")

        # 下注设置
        setting_actions = {
            "设置起步金额": "set_base",
            "设置递增金额": "set_increment",
            "设置最大连输": "set_max_losses",
            "设置止盈目标": "set_profit_target",
            "设置止损目标": "set_loss_target",
            "设置下注延迟": "set_bet_delay",
            "设置轮询间隔": "set_poll",
        }
        if text in setting_actions:
            s.pending_action = setting_actions[text]; s.save()
            return await self._reply(update, f"请输入{text[2:]}:")

        if text == "重置今日盈亏":
            return await self.cmd_reset_daily(update, None)

        # 特殊项
        if text == "0 开关/金额":
            s.pending_action = "special_0"; s.save()
            return await self._reply(update, "输入 on / off / 金额", Keyboard.SPECIAL_SUB)
        if text == "27 开关/金额":
            s.pending_action = "special_27"; s.save()
            return await self._reply(update, "输入 on / off / 金额", Keyboard.SPECIAL_SUB)
        if text == "豹子 开关/金额":
            s.pending_action = "special_baozi"; s.save()
            return await self._reply(update, "输入 on / off / 金额", Keyboard.SPECIAL_SUB)
        if text == "返回特殊项设置":
            return await self.cmd_special_show(update, None)

        # 13/14
        if text == "开启13/14":
            s.trigger_enabled = True; s.save()
            return await self._reply(update, "✅ 13/14倍投已开启", Keyboard.TRIGGER)
        if text == "关闭13/14":
            s.trigger_enabled = False; s.save()
            return await self._reply(update, "❌ 13/14倍投已关闭", Keyboard.TRIGGER)
        if text == "设置触发倍数":
            s.pending_action = "trigger_multiplier"; s.save()
            return await self._reply(update, "请输入触发倍数 (如 2.5):")

        await self._reply(update, "❓ 请点击下方按钮操作", Keyboard.MAIN)

    async def _resolve_pending(self, update: Update, text: str):
        uid = update.effective_chat.id
        s = self._user(uid)
        action = s.pending_action
        s.pending_action = None
        s.save()

        if action == "add_group":
            if not s.telegram_client:
                return await self._reply(update, "❌ 请先登录", Keyboard.MAIN)
            gid = await self._resolve_group(s, text)
            if gid is None:
                return await self._reply(update, "❌ 无法解析群组", Keyboard.GROUP)
            if gid in s.target_group_ids:
                return await self._reply(update, f"❌ 群组 {gid} 已存在", Keyboard.GROUP)
            s.target_group_ids.append(gid)
            s.group_multipliers[gid] = 1.0
            s.save()
            return await self._reply(update, f"✅ 已添加 {gid} (×1.00)", Keyboard.GROUP)

        if action == "set_default_group":
            if not s.telegram_client:
                return await self._reply(update, "❌ 请先登录", Keyboard.MAIN)
            gid = await self._resolve_group(s, text)
            if gid is None:
                return await self._reply(update, "❌ 无法解析群组", Keyboard.GROUP)
            s.default_group_id = gid
            if gid not in s.target_group_ids:
                s.target_group_ids.append(gid)
                s.group_multipliers[gid] = 1.0
            s.save()
            return await self._reply(update, f"✅ 默认群组 → {gid}", Keyboard.GROUP)

        if action == "set_multiplier":
            parts = text.split()
            if len(parts) != 2:
                return await self._reply(update, "❌ 格式: 群ID 倍数", Keyboard.GROUP)
            try:
                gid, mult = int(parts[0]), float(parts[1])
            except ValueError:
                return await self._reply(update, "❌ 格式错误", Keyboard.GROUP)
            if mult <= 0:
                return await self._reply(update, "❌ 倍数 > 0", Keyboard.GROUP)
            if gid not in s.target_group_ids:
                return await self._reply(update, f"❌ 群组 {gid} 不在列表中", Keyboard.GROUP)
            s.group_multipliers[gid] = mult
            s.save()
            return await self._reply(update, f"✅ {gid} → ×{mult:.2f}", Keyboard.GROUP)

        if action in self._NUMERIC_SETTINGS:
            return await self._handle_numeric_setting(update, action, text)

        if action in self._SPECIAL_MAP:
            en_attr, amt_attr = self._SPECIAL_MAP[action]
            low = text.lower()
            if low in ("on", "off"):
                setattr(s, en_attr, low == "on")
                s.save()
                return await self._reply(update, f"✅ {'启用' if low == 'on' else '关闭'}", Keyboard.SPECIAL)
            try:
                amt = int(text)
                if amt < 0:
                    raise ValueError
                setattr(s, amt_attr, amt)
                s.save()
                return await self._reply(update, f"✅ 金额 → {amt} KK", Keyboard.SPECIAL)
            except ValueError:
                return await self._reply(update, "❌ 输入无效", Keyboard.SPECIAL)

        await self._reply(update, "⚠️ 操作已过期，请重新选择", Keyboard.MAIN)

    # ═══════════════════════════════════════════════════════
    # SECTION 8 — 管理后台
    # ═══════════════════════════════════════════════════════

    def _load_all_users(self) -> List[Dict]:
        users = []
        for fn in os.listdir(Config.DATA_DIR):
            if not fn.startswith("user_") or not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(Config.DATA_DIR, fn)) as f:
                    d = json.load(f)
                uid = int(fn.split("_")[1].split(".")[0])
                users.append(dict(
                    user_id=uid,
                    balance=d.get("balance", 0),
                    usdt_balance=d.get("usdt_balance", 0),
                    cny_balance=d.get("cny_balance", 0),
                    kkcoin_balance=d.get("kkcoin_balance", 0),
                    daily_profit=d.get("daily_profit", 0),
                    betting_enabled=d.get("betting_enabled", False),
                    account_name=d.get("account_name", "未登录"),
                    algorithm=d.get("algorithm", "cit"),
                    target_groups=d.get("target_group_ids", []),
                    trigger_enabled=d.get("trigger_enabled", False),
                    trigger_multiplier=d.get("trigger_multiplier", Config.DEFAULT_TRIGGER_MULT),
                    client_phone=d.get("client_phone", ""),
                ))
            except Exception:
                continue
        return users

    def _admin_overview(self, users: List[Dict]) -> str:
        total = len(users)
        active = sum(1 for u in users if u["account_name"] != "未登录")
        running = sum(1 for u in users if u["betting_enabled"])
        kk = sum(u["kkcoin_balance"] for u in users)
        usdt = sum(u["usdt_balance"] for u in users)
        cny = sum(u["cny_balance"] for u in users)
        daily = sum(u["daily_profit"] for u in users)
        profit_n = sum(1 for u in users if u["daily_profit"] > 0)
        loss_n = sum(1 for u in users if u["daily_profit"] < 0)
        return (
            f"╔══════════════════════════════════╗\n"
            f"║     📊 管理后台 · 总体统计       ║\n"
            f"╚══════════════════════════════════╝\n\n"
            f"👥 总用户: {total}   🔗 已登录: {active}   ▶️  挂机中: {running}\n\n"
            f"💰 KKCOIN 总额: {kk:,.3f}\n"
            f"💵 USDT 总额:   {usdt:,.3f}\n"
            f"💴 CNY 总额:    {cny:,.3f}\n\n"
            f"📈 盈利: {profit_n}人   📉 亏损: {loss_n}人\n"
            f"📊 总今日盈亏: {daily:+,.3f} KKCOIN\n\n"
            f"💡 点击下方按钮查看详情"
        )

    def _admin_user_list(self, users: List[Dict], title: str, max_show: int = 20) -> str:
        if not users:
            return f"📋 {title}\n\n暂无数据"
        algo_label = {"cit": "CIT", "beitian_5y": "5Y", "beitian_7y": "7Y", "beitian_hybrid": "混合"}
        lines = [f"━━━ 📋 {title} ━━━", f"共 {len(users)} 人，显示前 {max_show} 名", "─" * 40]
        for i, u in enumerate(users[:max_show], 1):
            status = "🟢" if u["betting_enabled"] else "🔴"
            lines.append(
                f"{i}. {u['account_name']} (ID:{u['user_id']}) {status}\n"
                f"   ├─ KKCOIN: {u['kkcoin_balance']:,.3f}  USDT: {u['usdt_balance']:,.3f}  CNY: {u['cny_balance']:,.3f}\n"
                f"   ├─ 今日: {u['daily_profit']:+,.3f}  算法: {algo_label.get(u['algorithm'], '?')}\n"
                f"   └─ 群组: {len(u['target_groups'])}个"
            )
        return "\n".join(lines)

    async def cmd_admin(self, update: Update, _):
        if update.effective_chat.id != Config.OWNER_ID:
            return await self._reply(update, "⛔ 权限不足")
        users = self._load_all_users()
        await self._reply(update, self._admin_overview(users), Keyboard.admin_menu())

    async def cmd_admin_user(self, update: Update, ctx):
        if update.effective_chat.id != Config.OWNER_ID:
            return await self._reply(update, "⛔ 权限不足")
        if not ctx.args:
            return await self._reply(update, "❌ /admin_user <用户ID>")
        try:
            uid = int(ctx.args[0])
        except ValueError:
            return await self._reply(update, "❌ 用户ID应为整数")
        s = self._user(uid)
        algo_label = {"cit": "CIT杀组", "beitian_5y": "5Y", "beitian_7y": "7Y", "beitian_hybrid": "混合"}
        await self._reply(update, (
            f"━━━ 👤 用户详情 ━━━\n\n"
            f"🆔 ID: {uid}\n"
            f"👤 {s.account_name}\n"
            f"📱 {s.client_phone or '未登录'}\n\n"
            f"💎 KKCOIN: {s.kkcoin_balance:,.3f}\n"
            f"💵 USDT: {s.usdt_balance:,.3f}\n"
            f"💴 CNY: {s.cny_balance:,.3f}\n"
            f"📊 今日盈亏: {s.daily_profit:+,.3f}\n\n"
            f"▶️  {'挂机中' if s.betting_enabled else '已停止'}   "
            f"🧠 {algo_label.get(s.algorithm, '?')}   "
            f"⚡ 13/14: {'开' if s.trigger_enabled else '关'} ×{s.trigger_multiplier:.2f}\n\n"
            f"🎯 起步: {s.base_bet_amount}  递增: {s.martin_increment}  连输上限: {s.max_consecutive_losses}\n"
            f"📈 止盈: {s.profit_target:,.0f}  止损: {s.loss_target:,.0f}\n\n"
            f"📢 群组: {s.target_group_ids or '未设置'}\n"
            f"⭐ 默认: {s.default_group_id or '未设置'}"
        ))

    async def _admin_callback(self, update: Update, _):
        q = update.callback_query
        await q.answer()
        if q.from_user.id != Config.OWNER_ID:
            return await q.edit_message_text("⛔ 权限不足")

        data = q.data
        users = self._load_all_users()

        if data in ("admin_menu", "admin_refresh"):
            return await q.edit_message_text(
                self._admin_overview(users), reply_markup=Keyboard.admin_menu())

        actions = {
            "admin_overview":       lambda: (self._admin_overview(users), None),
            "admin_sort_balance":   lambda: (self._admin_user_list(sorted(users, key=lambda u: u["kkcoin_balance"], reverse=True), "按KKCOIN排序"), None),
            "admin_sort_daily":     lambda: (self._admin_user_list(sorted(users, key=lambda u: u["daily_profit"], reverse=True), "按今日盈亏排序"), None),
            "admin_sort_usdt":      lambda: (self._admin_user_list(sorted(users, key=lambda u: u["usdt_balance"], reverse=True), "按USDT排序"), None),
            "admin_sort_cny":       lambda: (self._admin_user_list(sorted(users, key=lambda u: u["cny_balance"], reverse=True), "按CNY排序"), None),
            "admin_filter_running": lambda: (self._admin_user_list([u for u in users if u["betting_enabled"]], "挂机中用户"), None),
            "admin_filter_stopped": lambda: (self._admin_user_list([u for u in users if not u["betting_enabled"]], "未挂机用户"), None),
            "admin_user_prompt":    lambda: ("🔍 使用 /admin_user <ID> 查看详情", None),
        }

        if data in actions:
            text, _ = actions[data]()
            await q.edit_message_text(text, reply_markup=Keyboard.admin_back())

    # ═══════════════════════════════════════════════════════
    # SECTION 9 — 核心循环
    # ═══════════════════════════════════════════════════════

    async def _settle(self, uid: int, state: UserState, draw: Draw) -> float:
        if state.last_processed_period == draw.period:
            return 0.0
        profit = await state.settle(draw)
        state.history.append(draw)
        if len(state.history) > 200:
            state.history = state.history[-200:]
        state.last_processed_period = draw.period
        # 更新准确率
        kill = predict_kill(state.history[:-1], state.algo)
        state.tracker.feed(kill, draw.group.value)
        state.save()
        Log.item(f"[结算] 期号:{draw.period} 号码:{draw} 盈亏:{profit:+.2f}")
        if profit != 0:
            await self._dm(uid,
                f"📊 {draw.period} 开奖: {draw}\n"
                f"💰 盈亏: {profit:+,.2f} KKCOIN\n"
                f"💎 余额: {state.kkcoin_balance:.3f} KKCOIN")
        return profit

    async def _query_balance(self, uid: int, state: UserState):
        if not state.telegram_client:
            return
        try:
            kkpay = await state.telegram_client.get_entity("@kkpay")
            sent = await state.telegram_client.send_message(kkpay, "/start")
            Log.sub(f"[余额查询] → @kkpay (/start)")
            await asyncio.sleep(3)
            msgs = await state.telegram_client.get_messages(kkpay, limit=10)
            resp = None
            for m in msgs:
                if m.id > sent.id and m.text and ("USDT" in m.text or "KKCOIN" in m.text):
                    resp = m.text
                    break
            if resp:
                usdt_m = re.search(r'USDT\s*[:：]\s*([\d,.]+)', resp, re.I)
                cny_m = re.search(r'CNY\s*[:：]\s*([\d,.]+)', resp, re.I)
                kk_m = re.search(r'KKCOIN\s*[:：]\s*([\d,.]+)', resp, re.I)
                if usdt_m:
                    state.usdt_balance = float(usdt_m.group(1).replace(',', ''))
                if cny_m:
                    state.cny_balance = float(cny_m.group(1).replace(',', ''))
                if kk_m:
                    new_kk = float(kk_m.group(1).replace(',', ''))
                    prev = state.kkcoin_balance if state.kkcoin_balance != 0 else state.balance
                    diff = new_kk - prev
                    state.kkcoin_balance = new_kk
                    state.balance = new_kk
                    state.last_balance = prev
                    today = date.today().isoformat()
                    if state.last_date != today:
                        state.daily_profit = 0.0
                        state.last_date = today
                    state.daily_profit += diff
                    state.save()
                    Log.sub(f"[余额] KKCOIN:{new_kk:.3f}  USDT:{state.usdt_balance:.3f}  CNY:{state.cny_balance:.3f}")
                    Log.end(f"[盈亏] 上次:{prev:.3f}  本次:{diff:+.3f}  今日:{state.daily_profit:+.3f}")
                    if diff != 0:
                        await self._dm(uid,
                            f"💰 余额更新\n"
                            f"  USDT: {state.usdt_balance:.3f}\n"
                            f"  CNY: {state.cny_balance:.3f}\n"
                            f"  KKCOIN: {state.kkcoin_balance:.3f}\n"
                            f"  盈亏: {diff:+.3f}  今日: {state.daily_profit:+.3f}")
                else:
                    Log.end("[余额解析] 未找到KKCOIN金额")
            else:
                Log.end("[余额解析] 未收到@kkpay回复")
        except Exception as e:
            Log.end(f"[余额异常] {e}")

    async def _place_bet(self, uid: int, state: UserState, draw: Draw):
        if not state.telegram_client or not state.betting_enabled:
            return
        kill = predict_kill(state.history, state.algo)
        try:
            remaining = [g for g in Group.all_groups() if g.value != kill]
        except StopIteration:
            remaining = list(Group.all_groups())

        if len(remaining) == 4:
            return Log.item("[无杀组] 四门全下，跳过")

        # 计算下期期号
        m = re.search(r'(\d+)$', draw.period)
        next_period = draw.period[:m.start()] + str(int(m.group(1)) + 1) if m else draw.period + "+1"

        kill_str = kill or "无"
        recommend = '、'.join(g.value for g in remaining)
        Log.item(f"[预测] 期号:{next_period}  杀组:{kill_str}  推荐:{recommend}")

        trigger = False
        if state.trigger_enabled and draw.sum_value in (13, 14):
            orig = state.current_recommend_amount
            state.current_recommend_amount = int(state.current_recommend_amount * state.trigger_multiplier)
            trigger = True
            Log.item(f"[触发] 13/14倍投  {orig} → {state.current_recommend_amount}")

        await asyncio.sleep(state.bet_delay_seconds)
        placed = await state.place_bet(next_period, remaining, trigger)

        groups_str = ", ".join(str(gid) for gid in state.target_group_ids)
        if placed:
            Log.item(f"[下注成功] 群组:{groups_str}  期号:{next_period}")
            items = [f"{g.value} {state.current_recommend_amount}" for g in remaining]
            items += [f"{n} {a}" for n, _, a in state.special_items()]
            msg = (f"✅ 已下注 → {groups_str}\n"
                   f"📌 杀组: {kill_str}  推荐: {'、'.join(items)}\n"
                   f"📌 期号: {next_period}")
            if trigger:
                msg += "\n⚠️ 13/14倍投触发"
            await self._dm(uid, msg)
        else:
            if state.consecutive_losses >= state.max_consecutive_losses and not trigger:
                Log.item(f"[暂停] 连输{state.consecutive_losses}次")
                await self._dm(uid, f"⚠️ 连输{state.consecutive_losses}次，暂停下注")
            else:
                Log.item("[下发失败]")
                await self._dm(uid, f"⚠️ 下注失败，请检查账号/群组")

    async def _main_loop(self):
        Log.banner("🚀 全局循环已启动")
        while self._running:
            try:
                draw = DrawAPI.fetch()
                if not draw:
                    await asyncio.sleep(30)
                    continue
                if self._global_last_period == draw.period:
                    await asyncio.sleep(5)
                    continue
                self._global_last_period = draw.period
                Log.banner(f"🆕 期号:{draw.period}  号码:{draw}")

                # 1. 结算
                settles = [self._settle(uid, s, draw) for uid, s in self._users.items() if s.telegram_client]
                if settles:
                    await asyncio.gather(*settles)

                await asyncio.sleep(15)

                # 2. 余额查询
                for uid, s in self._users.items():
                    if not s.telegram_client:
                        continue
                    Log.section(f"👤 {s.account_name}  (ID:{uid})")
                    await self._query_balance(uid, s)

                # 3. 下注
                for uid, s in self._users.items():
                    if not s.telegram_client:
                        continue
                    await self._place_bet(uid, s, draw)

                # 4. 汇总
                total_kk = sum(s.kkcoin_balance for s in self._users.values())
                total_daily = sum(s.daily_profit for s in self._users.values())
                Log.banner(f"📊 汇总 | 总KKCOIN:{total_kk:,.3f}  总今日盈亏:{total_daily:+,.3f}")

                await asyncio.sleep(30)

            except Exception as e:
                Log.error(f"[全局异常] {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)

    # ═══════════════════════════════════════════════════════
    # SECTION 10 — 启动
    # ═══════════════════════════════════════════════════════

    async def _build_app(self) -> Application:
        # 清理 webhook
        try:
            requests.get(f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteWebhook")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        app = Application.builder().token(Config.BOT_TOKEN).connect_timeout(30).read_timeout(30).build()
        await app.bot.delete_webhook(drop_pending_updates=True)

        # 注册 Handler
        handlers = [
            ("start", self.cmd_start), ("status", self.cmd_start), ("menu", self.cmd_start),
            ("go", self.cmd_go), ("stop", self.cmd_stop),
            ("sy", self.cmd_profit), ("zt", self.cmd_status),
            ("login", self.cmd_login), ("logout", self.cmd_logout),
            ("list_sessions", self.cmd_sessions),
            ("set_balance", self.cmd_set_balance),
            ("set_profit_target", self.cmd_set_profit_target),
            ("set_loss_target", self.cmd_set_loss_target),
            ("set_base", self.cmd_set_base),
            ("set_increment", self.cmd_set_increment),
            ("set_max_losses", self.cmd_set_max_losses),
            ("set_bet_delay", self.cmd_set_bet_delay),
            ("set_poll", self.cmd_set_poll),
            ("reset_daily", self.cmd_reset_daily),
            ("special", self.cmd_special_show), ("special_0", self.cmd_special_0),
            ("special_27", self.cmd_special_27), ("special_baozi", self.cmd_special_baozi),
            ("add_group", self.cmd_add_group), ("set_default_group", self.cmd_set_default_group),
            ("list_groups", self.cmd_list_groups), ("clear_groups", self.cmd_clear_groups),
            ("set_multiplier", self.cmd_set_multiplier),
            ("trigger", self.cmd_trigger),
            ("trigger_multiplier", self.cmd_trigger_multiplier),
            ("admin", self.cmd_admin), ("admin_user", self.cmd_admin_user),
        ]

        for name, handler in handlers:
            app.add_handler(CommandHandler(name, handler))

        app.add_handler(CallbackQueryHandler(self._admin_callback, pattern="^admin_"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._route_text))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        return app

    async def _route_text(self, update: Update, _):
        await self._route_button(update, update.message.text.strip())

    async def launch(self):
        Config.ensure_dirs()
        Log.init()
        self._app = await self._build_app()
        self._bot = self._app.bot
        asyncio.create_task(self._main_loop())
        await asyncio.Event().wait()

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════
# ENTRY
# ═══════════════════════════════════════════════════════════════

async def main():
    bot = Bot()
    try:
        await bot.launch()
    except KeyboardInterrupt:
        bot.stop()
        Log.info("🛑 已停止")


if __name__ == "__main__":
    asyncio.run(main())