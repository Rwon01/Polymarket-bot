import os
import time
import logging
import requests
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from web3 import Web3

# =====================
# ENV SETUP
# =====================

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://polygon-rpc.com")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))

# Fee-adjusted arbitrage threshold
ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.95"))
MAX_POSITION_USDC = float(os.getenv("MAX_POSITION_USDC", "20"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# =====================
# LOGGING SETUP
# =====================

LOG_FILE = "arb_bot.log"

logger = logging.getLogger("PolymarketArbBot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S"
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5_000_000, backupCount=3
)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# =====================
# CLIENT SETUP
# =====================

w3 = Web3(Web3.HTTPProvider(RPC_URL))

client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=CHAIN_ID
)

# =====================
# API HELPERS
# =====================

def get_markets():
    r = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"active": "true", "closed": "false"},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

def get_binary_assets(market):
    assets = market.get("assets", [])
    if len(assets) != 2:
        return None
    return assets[0], assets[1]

def get_best_ask(token_id):
    book = client.get_order_book(token_id)
    if not book.asks:
        return None
    return float(book.asks[0].price)

def place_ioc_buy(token_id, price, usdc_amount):
    logger.info(
        f"IOC BUY | token={token_id} | price={price:.4f} | size=${usdc_amount}"
    )
    return client.create_order(
        token_id=token_id,
        price=price,
        size=usdc_amount,
        side="buy",
        order_type="limit",
        time_in_force="IOC"
    )

# =====================
# CORE ARBITRAGE LOGIC
# =====================

def arbitrage_cycle():
    markets = get_markets()
    logger.info(f"Scanning {len(markets)} markets")

    for market in markets:
        pair = get_binary_assets(market)
        if not pair:
            continue

        yes, no = pair

        yes_ask = get_best_ask(yes["token_id"])
        no_ask = get_best_ask(no["token_id"])

        if yes_ask is None or no_ask is None:
            continue

        price_sum = yes_ask + no_ask

        if price_sum <= ENTRY_THRESHOLD:
            logger.info(
                f"ARB FOUND | {market.get('slug')} | "
                f"YES={yes_ask:.4f} NO={no_ask:.4f} "
                f"SUM={price_sum:.4f}"
            )

            try:
                place_ioc_buy(
                    yes["token_id"], yes_ask, MAX_POSITION_USDC
                )
                place_ioc_buy(
                    no["token_id"], no_ask, MAX_POSITION_USDC
                )

                logger.info("ARB EXECUTED — holding to settlement")
                return  # one arb at a time

            except Exception:
                logger.exception("Arbitrage execution failed")
                return

# =====================
# MAIN LOOP
# =====================

def main():
    logger.info("=== Polymarket Arbitrage Bot Started ===")
    logger.info(f"ENTRY_THRESHOLD={ENTRY_THRESHOLD}")
    logger.info(f"MAX_POSITION_USDC={MAX_POSITION_USDC}")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s")

    while True:
        try:
            arbitrage_cycle()
        except Exception:
            logger.exception("Fatal error in arbitrage cycle")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
