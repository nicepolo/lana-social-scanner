"""
smart_money.py — 鏈上聰明錢追蹤
監控已知大戶/聰明錢錢包，有大額買入陌生幣就推播
支援 Solana (SolanaTracker API) + BSC (BscScan API)
"""

import os, logging, requests, time
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

SOLANA_TRACKER_KEY = os.getenv("SOLANA_TRACKER_KEY", "")
BSCSCAN_KEY        = os.getenv("BSCSCAN_KEY", "")
TZ_TAIPEI          = timezone(timedelta(hours=8))

# ── 已知聰明錢錢包清單 ────────────────────────────────────────
# 格式：{"addr": "錢包地址", "label": "標籤", "chain": "solana|bsc"}
# 這裡預設幾個知名的，可以隨時新增
SMART_WALLETS = [
    # Solana 知名大戶/早期買手
    {"addr": "5tzFkiKscXHK5ZXCGbRe3vBQKDRrZXkgAzFSMkK1xhBP", "label": "SOL大戶A", "chain": "solana"},
    {"addr": "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMJ", "label": "SOL大戶B", "chain": "solana"},
    {"addr": "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "label": "Meme早買手", "chain": "solana"},
    # BSC 知名大戶（可補充）
    {"addr": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3", "label": "BSC大戶A", "chain": "bsc"},
    {"addr": "0x28c6c06298d514db089934071355e5743bf21d60", "label": "幣安熱錢包", "chain": "bsc"},
]

# 最低追蹤金額（美元）
MIN_BUY_USD = float(os.getenv("SMART_MONEY_MIN_USD", "5000"))

# 排除已知的主流幣（只追蹤陌生幣）
MAJOR_TOKENS = {
    "SOL", "BNB", "ETH", "BTC", "USDT", "USDC", "BUSD",
    "WBNB", "WETH", "WSOL", "DAI", "SHIB", "DOGE", "PEPE",
    "WIF", "BONK", "FLOKI", "MEME", "NEIRO"
}


def scan_smart_wallets(okx_listed: set, bnb_listed: set) -> list:
    """掃描所有聰明錢錢包的最新交易，回傳值得關注的買入"""
    results = []
    for wallet in SMART_WALLETS:
        try:
            if wallet["chain"] == "solana":
                txs = _get_solana_trades(wallet["addr"])
            else:
                txs = _get_bsc_trades(wallet["addr"])

            for tx in txs:
                sym = tx.get("symbol", "").upper()
                usd_val = tx.get("usd_value", 0)
                action  = tx.get("action", "")

                # 只看買入
                if action != "buy":
                    continue

                # 金額要夠大
                if usd_val < MIN_BUY_USD:
                    continue

                # 排除主流幣
                if sym in MAJOR_TOKENS:
                    continue

                # 檢查是否已上架
                on_okx = sym in okx_listed
                on_bnb = sym in bnb_listed

                # 兩家都沒有才是最強訊號
                if on_okx and on_bnb:
                    continue

                tx["wallet_label"] = wallet["label"]
                tx["wallet_addr"]  = wallet["addr"]
                tx["chain"]        = wallet["chain"]
                tx["on_okx"]       = on_okx
                tx["on_binance"]   = on_bnb
                results.append(tx)
                log.info(f"🐋 [{wallet['label']}] 買入 {sym} ${usd_val:,.0f}")

        except Exception as e:
            log.error(f"掃描錢包 {wallet['label']} 失敗: {e}")
        time.sleep(0.3)  # 避免 API rate limit

    return results


def _get_solana_trades(wallet_addr: str) -> list:
    """用 SolanaTracker API 取最近交易"""
    if not SOLANA_TRACKER_KEY:
        return _get_solana_trades_free(wallet_addr)

    try:
        url = f"https://data.solanatracker.io/wallet/{wallet_addr}/trades"
        headers = {"x-api-key": SOLANA_TRACKER_KEY}
        params  = {"limit": 20}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        trades = data.get("trades", []) if isinstance(data, dict) else data

        results = []
        now_ts = datetime.now(timezone.utc).timestamp()

        for t in trades[:20]:
            # 只看1小時內的交易
            tx_time = t.get("blockTime", 0) or t.get("timestamp", 0)
            if tx_time and (now_ts - tx_time) > 3600:
                continue

            token_in  = t.get("tokenIn",  {})
            token_out = t.get("tokenOut", {})

            # 判斷買入：用 SOL/USDC 換其他幣
            if token_in.get("symbol") in ["SOL", "USDC", "USDT"]:
                sym      = token_out.get("symbol", "")
                addr     = token_out.get("mint", "")
                usd_val  = float(t.get("volumeUsd", 0) or 0)
                price    = float(token_out.get("priceUsd", 0) or 0)

                if sym and usd_val > 0:
                    results.append({
                        "action":    "buy",
                        "symbol":    sym.upper(),
                        "token_addr": addr,
                        "usd_value": usd_val,
                        "price":     price,
                        "tx_hash":   t.get("txHash", ""),
                        "timestamp": tx_time,
                    })

        return results

    except Exception as e:
        log.error(f"SolanaTracker API 失敗: {e}")
        return []


def _get_solana_trades_free(wallet_addr: str) -> list:
    """無 API Key 時用 Solscan 免費接口"""
    try:
        url = f"https://public-api.solscan.io/account/transactions"
        params = {"account": wallet_addr, "limit": 20}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if not r.ok:
            return []
        # Solscan 免費版資料有限，先回傳空陣列
        return []
    except:
        return []


def _get_bsc_trades(wallet_addr: str) -> list:
    """用 BscScan API 取最近代幣轉帳"""
    try:
        url = "https://api.bscscan.com/api"
        params = {
            "module":    "account",
            "action":    "tokentx",
            "address":   wallet_addr,
            "sort":      "desc",
            "offset":    30,
            "page":      1,
        }
        if BSCSCAN_KEY:
            params["apikey"] = BSCSCAN_KEY

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "1":
            return []

        results = []
        now_ts  = datetime.now(timezone.utc).timestamp()

        for tx in data.get("result", [])[:30]:
            tx_time = int(tx.get("timeStamp", 0))
            if (now_ts - tx_time) > 3600:
                continue

            # 收到代幣 = 買入
            if tx.get("to", "").lower() == wallet_addr.lower():
                sym      = tx.get("tokenSymbol", "").upper()
                addr     = tx.get("contractAddress", "")
                decimals = int(tx.get("tokenDecimal", 18))
                value    = int(tx.get("value", 0)) / (10 ** decimals)

                # 估算 USD（沒有即時價格，先用 0 過濾）
                # 之後可串 CoinGecko 補價格
                results.append({
                    "action":    "buy",
                    "symbol":    sym,
                    "token_addr": addr,
                    "usd_value": 0,  # BSC 版暫時沒有 USD 估值
                    "price":     0,
                    "tx_hash":   tx.get("hash", ""),
                    "timestamp": tx_time,
                    "raw_value": value,
                })

        return results

    except Exception as e:
        log.error(f"BscScan API 失敗: {e}")
        return []


def add_wallet(addr: str, label: str, chain: str):
    """動態新增追蹤錢包"""
    SMART_WALLETS.append({"addr": addr, "label": label, "chain": chain})
    log.info(f"新增追蹤錢包: {label} ({chain})")


def get_wallet_list() -> list:
    return SMART_WALLETS.copy()
