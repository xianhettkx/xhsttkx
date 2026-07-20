import os
import json
import asyncio
import logging
import random
from enum import Enum
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
import aiohttp
from telethon import TelegramClient, events, Button
import socks

# ==================== 1. 全局配置信息 ====================

API_ID = 38252668
API_HASH = '7bfa9f824e18cd5498b984ee391de2e9'
BOT_TOKEN = "8987076623:AAGYfKZMcv-ox10XVpYmpfoTPyoInQgWgLg"
# SOCKS5 代理配置列表
PROXY_LIST = [
    {'addr': '8.138.35.134', 'port': 443, 'username': 'xxx', 'password': 'xxx'},
    {'addr': '8.163.67.73', 'port': 443, 'username': 'xxx', 'password': 'xxx'}
]

DATA_API_URL = "https://pc28.help/api/kj.json"
SESSIONS_DIR = "telegram_sessions"
USER_DATA_DIR = "user_data"

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

# ==================== 2. 数据结构与模型 ====================

class BetMethod(Enum):
    FLAT = "flat"            # 平注
    MARTINGALE = "martingale"# 马丁格尔倍投 (1, 2, 4, 8...)
    FIBONACCI = "fibonacci"  # 斐波那契缆法 (1, 1, 2, 3, 5, 8...)

@dataclass
class MarketData:
    issue_id: str
    number_str: str
    num_value: int
    combination: str

@dataclass
class EventSignal:
    user_id: int
    issue_id: str
    predict_target: str

@dataclass
class EventOrder:
    user_id: int
    issue_id: str
    target_group: str
    content: str
    amount: float

# ==================== 3. 多缆法风控引擎 ====================

class RiskManager:
    FIB_SEQUENCE = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]

    def __init__(
        self,
        base_amount: float = 100.0,
        daily_stop_loss: float = 2000.0,
        daily_stop_profit: float = 3000.0,
        max_consecutive_losses: int = 5,
        method: BetMethod = BetMethod.MARTINGALE
    ):
        self.base_amount = base_amount
        self.daily_stop_loss = daily_stop_loss
        self.daily_stop_profit = daily_stop_profit
        self.max_consecutive_losses = max_consecutive_losses
        self.method = method

        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.fib_index = 0
        self.is_fused = False

    def calculate_bet_amount(self) -> float:
        """计算下一次注码"""
        if self.method == BetMethod.FLAT:
            return self.base_amount
        elif self.method == BetMethod.MARTINGALE:
            return self.base_amount * (2 ** self.consecutive_losses)
        elif self.method == BetMethod.FIBONACCI:
            idx = min(self.fib_index, len(self.FIB_SEQUENCE) - 1)
            return self.base_amount * self.FIB_SEQUENCE[idx]
        return self.base_amount

    def can_bet(self) -> tuple[bool, str]:
        """风控校验检查"""
        if self.is_fused:
            return False, "⚠️ 触发连续亏损熔断,停止下注"
        if self.daily_pnl <= -self.daily_stop_loss:
            return False, f"🛑 触及每日硬止损 (-{self.daily_stop_loss})"
        if self.daily_pnl >= self.daily_stop_profit:
            return False, f"🎉 达到每日止盈线 (+{self.daily_stop_profit})"
        return True, "OK"

    def on_settlement(self, is_win: bool, odds: float = 1.95):
        """开奖结算与状态更新"""
        current_bet = self.calculate_bet_amount()
        if is_win:
            pnl = current_bet * (odds - 1.0)
            self.daily_pnl += pnl
            self.consecutive_losses = 0
            self.fib_index = max(0, self.fib_index - 2)
            logger.info(f"✅ 盈利: +{pnl:.2f} | 今日累计: {self.daily_pnl:.2f}")
        else:
            pnl = -current_bet
            self.daily_pnl += pnl
            self.consecutive_losses += 1
            self.fib_index += 1
            logger.warning(f"❌ 亏损: {pnl:.2f} | 连亏: {self.consecutive_losses}次 | 今日累计: {self.daily_pnl:.2f}")

            if self.consecutive_losses >= self.max_consecutive_losses:
                self.is_fused = True
                logger.error(f"🚨 触发连续 {self.max_consecutive_losses} 次亏损熔断保护!")

    def to_dict(self) -> dict:
        return {
            "base_amount": self.base_amount,
            "daily_stop_loss": self.daily_stop_loss,
            "daily_stop_profit": self.daily_stop_profit,
            "max_consecutive_losses": self.max_consecutive_losses,
            "method": self.method.value,
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "fib_index": self.fib_index,
            "is_fused": self.is_fused
        }

    @classmethod
    def from_dict(cls, data: dict):
        rm = cls(
            base_amount=data.get("base_amount", 100.0),
            daily_stop_loss=data.get("daily_stop_loss", 2000.0),
            daily_stop_profit=data.get("daily_stop_profit", 3000.0),
            max_consecutive_losses=data.get("max_consecutive_losses", 5),
            method=BetMethod(data.get("method", "martingale"))
        )
        rm.daily_pnl = data.get("daily_pnl", 0.0)
        rm.consecutive_losses = data.get("consecutive_losses", 0)
        rm.fib_index = data.get("fib_index", 0)
        rm.is_fused = data.get("is_fused", False)
        return rm

# ==================== 4. 用户数据持久化 ====================

class UserState:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.file_path = os.path.join(USER_DATA_DIR, f"{user_id}.json")
        self.is_logged_in = False
        self.is_active = False
        self.phone = ""
        self.groups: List[str] = []
        self.algorithm = "default"
        self.history: List[dict] = []
        self.risk_mgr = RiskManager()
        self.client: Optional[TelegramClient] = None
        self.temp_phone_code_hash = None

        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.is_logged_in = data.get("is_logged_in", False)
                    self.is_active = data.get("is_active", False)
                    self.phone = data.get("phone", "")
                    self.groups = data.get("groups", [])
                    self.algorithm = data.get("algorithm", "default")
                    self.history = data.get("history", [])
                    if "risk_mgr" in data:
                        self.risk_mgr = RiskManager.from_dict(data["risk_mgr"])
            except Exception as e:
                logger.error(f"读取用户 {self.user_id} 配置失败: {e}")

    def save(self):
        data = {
            "user_id": self.user_id,
            "is_logged_in": self.is_logged_in,
            "is_active": self.is_active,
            "phone": self.phone,
            "groups": self.groups,
            "algorithm": self.algorithm,
            "history": self.history,
            "risk_mgr": self.risk_mgr.to_dict()
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# ==================== 5. 策略预测接口 ====================

def predict(history: List[dict], algo: str) -> str:
    """预测算法接口"""
    if not history:
        return "A类"
    
    recent = history[:5]
    a_count = sum(1 for item in recent if "大" in item.get("combination", ""))
    
    if algo == "counter":
        return "B类" if a_count >= 3 else "A类"
    return "A类" if a_count >= 3 else "B类"

# ==================== 6. 数据源与网络获取 ====================

class DataFetcher:
    @staticmethod
    async def fetch_latest() -> Optional[MarketData]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(DATA_API_URL, timeout=10) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        data_list = res.get("data", [])
                        if data_list:
                            raw = data_list[0]
                            return MarketData(
                                issue_id=str(raw.get("nbr")),
                                number_str=str(raw.get("number")),
                                num_value=int(raw.get("num", 0)),
                                combination=str(raw.get("combination", ""))
                            )
        except Exception as e:
            logger.error(f"提取 API 数据异常: {e}")
        return None

# ==================== 7. 主系统核心 (Bot UI + 事件总线) ====================

class SystemOrchestrator:
    def __init__(self):
        self.bot = TelegramClient("telegram_sessions/bot_master", API_ID, API_HASH)
        self.users: Dict[int, UserState] = {}
        self.user_login_states: Dict[int, str] = {}
        self.last_issue_id: Optional[str] = None
        self.executed_issues: set = set()

        # 事件驱动管道队列
        self.data_queue = asyncio.Queue()
        self.signal_queue = asyncio.Queue()
        self.order_queue = asyncio.Queue()

    def get_user_state(self, user_id: int) -> UserState:
        if user_id not in self.users:
            self.users[user_id] = UserState(user_id)
        return self.users[user_id]

    def get_random_proxy(self):
        """构造 SOCKS5 代理参数"""
        if not PROXY_LIST:
            return None
        p = random.choice(PROXY_LIST)
        return (socks.SOCKS5, p['addr'], int(p['port']), True, p['username'], p['password'])

    # ---------------- UI 菜单 ----------------

    def main_keyboard(self, u_state: UserState):
        status_text = "🟢 运行中" if u_state.is_active else "🔴 已停止"
        login_text = "🚪 登出账号" if u_state.is_logged_in else "🔑 登录账号"
        method_name = u_state.risk_mgr.method.value.upper()
        
        return [
            [Button.inline(f"服务状态: {status_text}"), Button.inline(login_text, data=b"toggle_login")],
            [Button.inline("▶️ 启动服务", data=b"srv_start"), Button.inline("⏹️ 停止服务", data=b"srv_stop")],
            [Button.inline("➕ 添加群组", data=b"group_add"), Button.inline("📋 群组列表", data=b"group_list")],
            [Button.inline(f"缆法: {method_name}", data=b"switch_method"), Button.inline(f"策略: {u_state.algorithm}", data=b"switch_algo")]
        ]

    # ---------------- Telegram Bot 事件处理 ----------------

    async def register_handlers(self):
        
        @self.bot.on(events.NewMessage(pattern="/start"))
        async def handler_start(event):
            sender_id = event.sender_id
            u_state = self.get_user_state(sender_id)
            await event.respond(
                f"👋 **欢迎使用自动投注与数据分析系统**\n您的 User ID: `{sender_id}`",
                buttons=self.main_keyboard(u_state)
            )

        @self.bot.on(events.CallbackQuery)
        async def handler_callback(event):
            sender_id = event.sender_id
            u_state = self.get_user_state(sender_id)
            data = event.data

            if data == b"srv_start":
                if not u_state.is_logged_in:
                    await event.answer("❌ 请先完成 Telegram 账号登录!", alert=True)
                    return
                u_state.is_active = True
                u_state.save()
                await event.edit("✅ 自动下注与推送服务已启动!", buttons=self.main_keyboard(u_state))

            elif data == b"srv_stop":
                u_state.is_active = False
                u_state.save()
                await event.edit("🛑 服务已停止。", buttons=self.main_keyboard(u_state))

            elif data == b"toggle_login":
                if u_state.is_logged_in:
                    u_state.is_logged_in = False
                    u_state.is_active = False
                    if u_state.client:
                        await u_state.client.disconnect()
                        u_state.client = None
                    u_state.save()
                    await event.edit("已退出登录。", buttons=self.main_keyboard(u_state))
                else:
                    self.user_login_states[sender_id] = "WAITING_PHONE"
                    await event.respond("📱 请发送您的手机号码(含国家代码,例: `+8613800000000`):")

            elif data == b"group_add":
                self.user_login_states[sender_id] = "WAITING_GROUP"
                await event.respond("✏️ 请发送目标群组 ID 或 @username:")

            elif data == b"group_list":
                g_str = "\n".join(u_state.groups) if u_state.groups else "暂无关联群组"
                await event.respond(f"📋 **配置群组列表:**\n\n{g_str}")

            elif data == b"switch_method":
                methods = list(BetMethod)
                curr_idx = methods.index(u_state.risk_mgr.method)
                next_method = methods[(curr_idx + 1) % len(methods)]
                u_state.risk_mgr.method = next_method
                u_state.save()
                await event.edit(f"已切换风控注码缆法为: **{next_method.value.upper()}**", buttons=self.main_keyboard(u_state))

            elif data == b"switch_algo":
                u_state.algorithm = "counter" if u_state.algorithm == "default" else "default"
                u_state.save()
                await event.edit(f"已切换策略预测算法为: **{u_state.algorithm}**", buttons=self.main_keyboard(u_state))

        @self.bot.on(events.NewMessage)
        async def handler_text_input(event):
            if event.text.startswith("/"):
                return
            sender_id = event.sender_id
            state_flag = self.user_login_states.get(sender_id)
            u_state = self.get_user_state(sender_id)

            if state_flag == "WAITING_PHONE":
                phone = event.text.strip()
                u_state.phone = phone
                session_path = os.path.join(SESSIONS_DIR, f"user_{sender_id}")
                proxy = self.get_random_proxy()

                try:
                    client = TelegramClient(session_path, API_ID, API_HASH, proxy=proxy)
                    await client.connect()
                    code_req = await client.send_code_request(phone)
                    
                    u_state.client = client
                    u_state.temp_phone_code_hash = code_req.phone_code_hash
                    
                    self.user_login_states[sender_id] = "WAITING_CODE"
                    await event.respond("📩 验证码已发送,请输入收到的短信/ Telegram 验证码:")
                except Exception as e:
                    await event.respond(f"❌ 请求失败: {e}")
                    self.user_login_states.pop(sender_id, None)

            elif state_flag == "WAITING_CODE":
                code = event.text.strip()
                try:
                    await u_state.client.sign_in(u_state.phone, code, phone_code_hash=u_state.temp_phone_code_hash)
                    u_state.is_logged_in = True
                    u_state.save()
                    self.user_login_states.pop(sender_id, None)
                    await event.respond("🎉 验证成功!账号已就绪。", buttons=self.main_keyboard(u_state))
                except Exception as e:
                    await event.respond(f"❌ 登录校验失败: {e}")
                    self.user_login_states.pop(sender_id, None)

            elif state_flag == "WAITING_GROUP":
                group = event.text.strip()
                if group not in u_state.groups:
                    u_state.groups.append(group)
                    u_state.save()
                    await event.respond(f"✅ 成功添加群组: `{group}`")
                self.user_login_states.pop(sender_id, None)

    # ---------------- 异步事件处理管道 Worker ----------------

    async def poll_api_worker(self):
        """数据拉取 Producer"""
        logger.info("📡 API 数据轮询线程启动...")
        while True:
            try:
                data = await DataFetcher.fetch_latest()
                if data and data.issue_id != self.last_issue_id:
                    logger.info(f"🆕 发现新开奖期号: {data.issue_id} | 结果: {data.number_str} ({data.combination})")
                    self.last_issue_id = data.issue_id
                    await self.data_queue.put(data)
            except Exception as e:
                logger.error(f"轮询错误: {e}")
            await asyncio.sleep(30)

    async def strategy_worker(self):
        """策略计算 Consumer / Signal Producer"""
        while True:
            data: MarketData = await self.data_queue.get()
            try:
                raw_dict = asdict(data)
                for uid, u_state in list(self.users.items()):
                    if u_state.is_active and u_state.is_logged_in:
                        # 1. 保存历史
                        u_state.history.insert(0, raw_dict)
                        if len(u_state.history) > 50:
                            u_state.history = u_state.history[:50]

                        # 2. 结算上期(假设 A类为赢方)
                        is_win = "大" in data.combination
                        u_state.risk_mgr.on_settlement(is_win=is_win)
                        u_state.save()

                        # 3. 产生预测信号
                        pred = predict(u_state.history, u_state.algorithm)
                        signal = EventSignal(user_id=uid, issue_id=data.issue_id, predict_target=pred)
                        await self.signal_queue.put(signal)
            finally:
                self.data_queue.task_done()

    async def risk_worker(self):
        """风控审核 Consumer / Order Producer"""
        while True:
            signal: EventSignal = await self.signal_queue.get()
            try:
                u_state = self.get_user_state(signal.user_id)
                can_bet, reason = u_state.risk_mgr.can_bet()

                if not can_bet:
                    logger.warning(f"用户 {signal.user_id} 风控拦截 [期号 {signal.issue_id}]: {reason}")
                    continue

                amount = u_state.risk_mgr.calculate_bet_amount()
                msg = f"{signal.predict_target} {int(amount)}"

                for group in u_state.groups:
                    order = EventOrder(
                        user_id=signal.user_id,
                        issue_id=signal.issue_id,
                        target_group=group,
                        content=msg,
                        amount=amount
                    )
                    await self.order_queue.put(order)
            finally:
                self.signal_queue.task_done()

    async def execution_worker(self):
        """最终网络下订单 Consumer (含重试逻辑)"""
        while True:
            order: EventOrder = await self.order_queue.get()
            try:
                u_state = self.get_user_state(order.user_id)
                if not u_state.client:
                    continue

                dedup_key = f"{order.user_id}_{order.issue_id}_{order.target_group}"
                if dedup_key in self.executed_issues:
                    continue

                success = False
                for attempt in range(1, 4):
                    try:
                        await u_state.client.send_message(order.target_group, order.content)
                        success = True
                        break
                    except Exception as e:
                        logger.warning(f"发送重试 ({attempt}/3) 失败: {e}")
                        await asyncio.sleep(1)

                if success:
                    self.executed_issues.add(dedup_key)
                    logger.info(f"🚀 [下单成功] 用户 {order.user_id} -> {order.target_group}: {order.content}")
                else:
                    logger.error(f"❌ [下单失败] 用户 {order.user_id} 无法推送到 {order.target_group}")
            finally:
                self.order_queue.task_done()

    # ---------------- 启动入口 ----------------

    async def start(self):
        # 1. 启动交互 Bot
        await self.bot.start(bot_token=BOT_TOKEN)
        await self.register_handlers()
        logger.info("🤖 主控 Telegram Bot 已成功启动!")

        # 2. 预载本地授权的用户 Client
        for file in os.listdir(USER_DATA_DIR):
            if file.endswith(".json"):
                uid = int(file.split(".")[0])
                u_state = self.get_user_state(uid)
                session_path = os.path.join(SESSIONS_DIR, f"user_{uid}")
                if u_state.is_logged_in and os.path.exists(f"{session_path}.session"):
                    proxy = self.get_random_proxy()
                    try:
                        client = TelegramClient(session_path, API_ID, API_HASH, proxy=proxy)
                        await client.connect()
                        if await client.is_user_authorized():
                            u_state.client = client
                            logger.info(f"恢复用户账号 Client {uid} 连接成功。")
                    except Exception as e:
                        logger.error(f"恢复用户 {uid} 连接异常: {e}")

        # 3. 挂载异步并发任务
        asyncio.create_task(self.poll_api_worker())
        asyncio.create_task(self.strategy_worker())
        asyncio.create_task(self.risk_worker())
        asyncio.create_task(self.execution_worker())

        # 保持长连接运行
        await self.bot.run_until_disconnected()

if __name__ == "__main__":
    orchestrator = SystemOrchestrator()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(orchestrator.start())
