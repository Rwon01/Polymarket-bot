import os
import time
import signal
import threading
import logging
import requests
from collections import deque
from dataclasses import dataclass
from typing import Dict, Deque, Optional

from dotenv import load_dotenv

# ======================================================
# ENV LOADING
# ======================================================

load_dotenv("example.env")

def env(name, cast=str, default=None, required=False):
    val = os.getenv(name)
    if val is None or val == "":
        if required:
            raise RuntimeError(f"Missing required env var: {name}")
        return default
    try:
        return cast(val)
    except Exception:
        raise RuntimeError(f"Invalid value for {name}: {val}")

# ======================================================
# WALLET / CONTRACT CONFIG
# ======================================================

PK = env("PK", str, default="")
PROXY_WALLET = env("YOUR_PROXY_WALLET", str, required=True)
TRADER_ADDRESS = env("BOT_TRADER_ADDRESS", str, required=True)

USDC_CONTRACT = env("USDC_CONTRACT_ADDRESS", str, required=True)
SETTLEMENT_CONTRACT = env("POLYMARKET_SETTLEMENT_CONTRACT", str, required=True)

PAPER_TRADING = PK.strip() == ""

# ======================================================
# STRATEGY CONFIG (MATCHES YOUR .env)
# ======================================================

SPIKE_THRESHOLD = env("spike_threshold", float, required=True)
PRICE_HISTORY_SIZE = env("price_history_size", int, required=True)
MIN_LIQUIDITY = env("min_liquidity_requirement", float, required=True)
COOLDOWN_SECONDS = env("cooldown_period", int, required=True)
MAX_CONCURRENT_TRADES = env("max_concurrent_trades", int, required=True)

TRADE_SIZE_USDC = env("trade_unit", float, required=True)
KEEP_MIN_SHARES = env("keep_min_shares", int, default=1)

HOLDING_TIME_LIMIT = env("holding_time_limit", int, required=True)
SOLD_POSITION_TIME = env("sold_position_time", int, default=1800)

TAKE_PROFIT_PCT = env("take_profit", float, required=True)
STOP_LOSS_PCT = env("stop_loss", float, required=True)

PCT_PROFIT = env("pct_profit", float, default=None)
CASH_PROFIT = env("cash_profit", float, default=None)

PCT_LOSS = env("pct_loss", float, default=None)
CASH_LOSS = env("cash_loss", float, default=None)

SLIPPAGE_TOLERANCE = env("slippage_tolerance", float, required=True)

# ======================================================
# TIMING
# ======================================================

MARKET_SCAN_INTERVAL = 60
PRICE_POLL_INTERVAL = 3
EXIT_CHECK_INTERVAL = 5

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("polybot")

# ======================================================
# DATA MODELS
# ======================================================

@dataclass
class MarketPair:
    yes: str
    no: str
    liquidity: float

@dataclass
class ActiveTrade:
    asset: str
    entry_price: float
    entry_time: float

# ======================================================
# THREAD SAFE STATE
# ======================================================

class BotState:
    def __init__(self):
        self.lock = threading.Lock()
        self.shutdown = False
        self.market_pairs: Dict[str, MarketPair] = {}
        self.price_history: Dict[str, Deque[float]] = {}
        self.active_trades: Dict[str, ActiveTrade] = {}
        self.cooldowns: Dict[str, float] = {}

    def set_shutdown(self):
        with self.lock:
            self.shutdown = True

    def is_shutdown(self):
        with self.lock:
            return self.shutdown

# ======================================================
# API HELPERS
# ======================================================

def fetch_active_markets():
    r = requests.get(
        "https://gamma-api.polymarket.com/markets?active=true&closed=false",
        timeout=15
    )
    r.raise_for_status()
    return r.json()

def fetch_prices():
    r = requests.get(
        "https://gamma-api.polymarket.com/prices",
        timeout=10
    )
    r.raise_for_status()
    return r.json()

# ======================================================
# THREADS
# ======================================================

def market_scanner(state: BotState):
    while not state.is_shutdown():
        try:
            markets = fetch_active_markets()
            with state.lock:
                for m in markets:
                    tokens = m.get("tokens", [])
                    if len(tokens) != 2:
                        continue

                    liquidity = float(m.get("liquidity", 0))
                    if liquidity < MIN_LIQUIDITY:
                        continue

                    yes, no = tokens
                    pair = MarketPair(
                        yes=yes["token_id"],
                        no=no["token_id"],
                        liquidity=liquidity
                    )

                    state.market_pairs[pair.yes] = pair
                    state.market_pairs[pair.no] = pair

                    state.price_history.setdefault(
                        pair.yes, deque(maxlen=PRICE_HISTORY_SIZE)
                    )
                    state.price_history.setdefault(
                        pair.no, deque(maxlen=PRICE_HISTORY_SIZE)
                    )

            logger.info(f"Market scan complete ({len(state.market_pairs)//2} pairs)")
        except Exception as e:
            logger.error(f"Market scanner error: {e}")

        time.sleep(MARKET_SCAN_INTERVAL)

def price_updater(state: BotState):
    while not state.is_shutdown():
        try:
            prices = fetch_prices()
            with state.lock:
                for asset, price in prices.items():
                    if asset in state.price_history:
                        state.price_history[asset].append(float(price))
        except Exception as e:
            logger.error(f"Price updater error: {e}")

        time.sleep(PRICE_POLL_INTERVAL)

def compute_delta(history: Deque[float]) -> Optional[float]:
    if len(history) < 2:
        return None
    prev, curr = history[-2], history[-1]
    if prev <= 0:
        return None
    return (curr - prev) / prev

# ======================================================
# EXECUTION (PAPER / LIVE STUB)
# ======================================================

def place_buy(asset: str) -> bool:
    tag = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info(f"[{tag}] BUY {asset}")
    return True

def place_sell(asset: str) -> bool:
    tag = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info(f"[{tag}] SELL {asset}")
    return True

# ======================================================
# SPIKE DETECTOR
# ======================================================

def spike_detector(state: BotState):
    while not state.is_shutdown():
        now = time.time()
        with state.lock:
            for asset, history in state.price_history.items():
                delta = compute_delta(history)
                if delta is None or delta < SPIKE_THRESHOLD:
                    continue

                if asset in state.active_trades:
                    continue

                if now - state.cooldowns.get(asset, 0) < COOLDOWN_SECONDS:
                    continue

                if len(state.active_trades) >= MAX_CONCURRENT_TRADES:
                    continue

                if place_buy(asset):
                    state.active_trades[asset] = ActiveTrade(
                        asset=asset,
                        entry_price=history[-1],
                        entry_time=now
                    )
                    state.cooldowns[asset] = now
                    logger.info(f"ENTER {asset} Δ={delta:.2%}")

        time.sleep(1)

# ======================================================
# EXIT MANAGER (PCT + CASH)
# ======================================================

def exit_manager(state: BotState):
    while not state.is_shutdown():
        now = time.time()
        with state.lock:
            for asset, trade in list(state.active_trades.items()):
                history = state.price_history.get(asset)
                if not history:
                    continue

                price = history[-1]
                pnl_pct = (price - trade.entry_price) / trade.entry_price
                pnl_cash = pnl_pct * TRADE_SIZE_USDC
                held = now - trade.entry_time

                exit_reason = None

                if PCT_PROFIT is not None and pnl_pct >= PCT_PROFIT:
                    exit_reason = "pct_profit"
                elif CASH_PROFIT is not None and pnl_cash >= CASH_PROFIT:
                    exit_reason = "cash_profit"
                elif PCT_LOSS is not None and pnl_pct <= PCT_LOSS:
                    exit_reason = "pct_loss"
                elif CASH_LOSS is not None and pnl_cash <= CASH_LOSS:
                    exit_reason = "cash_loss"
                elif held >= HOLDING_TIME_LIMIT:
                    exit_reason = "time"

                if exit_reason:
                    if place_sell(asset):
                        del state.active_trades[asset]
                        logger.info(
                            f"EXIT {asset} reason={exit_reason} "
                            f"pnl_pct={pnl_pct:.2%} pnl_cash={pnl_cash:.2f}"
                        )

        time.sleep(EXIT_CHECK_INTERVAL)

# ======================================================
# MAIN
# ======================================================

state = BotState()

def shutdown_handler(sig, frame):
    logger.warning("Shutdown signal received")
    state.set_shutdown()

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

threads = [
    threading.Thread(target=market_scanner, args=(state,), daemon=True),
    threading.Thread(target=price_updater, args=(state,), daemon=True),
    threading.Thread(target=spike_detector, args=(state,), daemon=True),
    threading.Thread(target=exit_manager, args=(state,), daemon=True),
]

for t in threads:
    t.start()

logger.info(
    f"Polymarket spike bot running "
    f"({'PAPER' if PAPER_TRADING else 'LIVE'})"
)

while not state.is_shutdown():
    time.sleep(1)

for t in threads:
    t.join()

logger.info("Bot stopped cleanly")
