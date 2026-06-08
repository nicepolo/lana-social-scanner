"""
LANA Social Scanner v1.0
土狗幣情緒交易偵測系統
GeckoTerminal + OKX + Binance 三層交叉比對
"""

import os, time, json, logging, threading, requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify
from flask_cors import CORS
import schedule
from dotenv import load_dotenv
from ai_risk import analyze_risk as analyze_token_risk
from notify_social import send_signal, send_tg_alpha_alert, send_smart_money_alert, send_kol_alert
from smart_money import scan_smart_wallets
from tg_monitor import start_monitoring, get_channel_list
from kol_monitor import scan_kol_mentions

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ── 設定 ──────────────────────────────────────────────────────
SCAN_INTERVAL_MIN   = int(os.getenv("SOCIAL_SCAN_INTERVAL_MIN", "5"))
MIN_VOLUME_USD      = float(os.getenv("MIN_VOLUME_USD", "50000"))      # 1h 最低成交量
MIN_PRICE_CHANGE    = float(os.getenv("MIN_PRICE_CHANGE", "15"))       # 1h 最低漲幅 %
MIN_RISK_SCORE      = int(os.getenv("MIN_RISK_SCORE", "50"))           # AI 風險分數門檻
CHAINS              = ["bsc", "solana"]                                 # 監控鏈
PORT                = int(os.getenv("PORT", "8081"))
TZ_TAIPEI           = timezone(timedelta(hours=8))

# ── 全域快取 ──────────────────────────────────────────────────
_cache = {
    "signals": [],          # 達標訊號
    "scanned": [],          # 所有掃描結果
    "last_update": None,
    "scan_count": 0,
    "okx_listed": set(),    # OKX 已上架幣種快取
    "bnb_listed": set(),    # Binance 已上架幣種快取
}
_lock = threading.Lock()

# ── API 端點 ──────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "LANA Social Scanner v1.0"})

@app.route("/api/social_signals")
def social_signals():
    with _lock:
        return jsonify({
            "signals":     _cache["signals"],
            "scanned":     _cache["scanned"][:20],
            "last_update": _cache["last_update"],
            "scan_count":  _cache["scan_count"],
        })

@app.route("/api/tg_channels")
def tg_channels():
    return jsonify({"channels": get_channel_list()})

@app.route("/api/debug")
def debug_info():
    with _lock:
        return jsonify({
            "scan_count": _cache.get("scan_count", 0),
            "last_update": _cache.get("last_update", ""),
            "signals_count": len(_cache.get("signals", [])),
            "scanned_count": len(_cache.get("scanned", [])),
            "okx_count": len(list(_cache.get("okx_listed", set()))),
            "bnb_count": len(list(_cache.get("bnb_listed", set()))),
            "scanned_sample": _cache.get("scanned", [])[:3],
        })

@app.route("/api/health")
def health():
    with _lock:
        return jsonify({
            "status": "ok",
            "last_update": _cache["last_update"],
            "signal_count": len(_cache["signals"]),
        })

# ── 交易所上架狀態 ────────────────────────────────────────────

def refresh_exchange_listings():
    """每小時刷新 OKX 和 Binance 的現貨上架清單"""
    try:
        # OKX 現貨幣種
        r = requests.get("https://www.okx.com/api/v5/public/instruments?instType=SPOT", timeout=10)
        data = r.json()
        okx = set()
        for inst in data.get("data", []):
            base = inst.get("baseCcy", "").upper()
            if base:
                okx.add(base)
        with _lock:
            _cache["okx_listed"] = okx
        log.info(f"OKX 現貨幣種數: {len(okx)}")
    except Exception as e:
        log.error(f"OKX 上架清單抓取失敗: {e}")

    try:
        # Binance 現貨幣種
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=10)
        data = r.json()
        bnb = set()
        for sym in data.get("symbols", []):
            if sym.get("quoteAsset") == "USDT" and sym.get("status") == "TRADING":
                bnb.add(sym.get("baseAsset", "").upper())
        with _lock:
            _cache["bnb_listed"] = bnb
        log.info(f"Binance 現貨幣種數: {len(bnb)}")
    except Exception as e:
        log.error(f"Binance 上架清單抓取失敗: {e}")


def get_listing_status(symbol: str) -> dict:
    """檢查幣種在各交易所的上架狀態"""
    sym = symbol.upper()
    with _lock:
        on_okx = sym in _cache["okx_listed"]
        on_bnb = sym in _cache["bnb_listed"]

    # 判斷訊號強度
    if not on_okx and not on_bnb:
        status = "UNLISTED"      # 兩家都沒有 → 最強訊號
        strength = "🔥🔥🔥 超強"
    elif on_okx and not on_bnb:
        status = "OKX_ONLY"     # OKX 有，幣安沒有 → 等幣安上架
        strength = "🔥🔥 強"
    elif not on_okx and on_bnb:
        status = "BNB_ONLY"     # 幣安有，OKX 沒有 → 次強
        strength = "🔥 中"
    else:
        status = "LISTED"        # 兩家都有 → 已晚
        strength = "⚪️ 已上架"

    return {
        "on_okx": on_okx,
        "on_binance": on_bnb,
        "status": status,
        "strength": strength,
    }

# ── GeckoTerminal 熱門新幣 ────────────────────────────────────

def fetch_trending_tokens(chain: str) -> list:
    """從 GeckoTerminal 抓鏈上熱門交易對"""
    try:
        # 抓過去1小時交易量最高的交易對
        url = f"https://api.geckoterminal.com/api/v2/networks/{chain}/trending_pools"
        params = {"page": 1}
        r = requests.get(url, params=params, timeout=10,
                        headers={"Accept": "application/json;version=20230302"})
        r.raise_for_status()
        data = r.json()
        pools = data.get("data", [])

        results = []
        for pool in pools[:30]:  # 取前30個
            attr = pool.get("attributes", {})
            try:
                symbol = attr.get("name", "").split("/")[0].strip()
                addr   = attr.get("address", "")
                price  = float(attr.get("base_token_price_usd", 0) or 0)
                vol_1h = float(attr.get("volume_usd", {}).get("h1", 0) or 0)
                vol_24h= float(attr.get("volume_usd", {}).get("h24", 0) or 0)
                chg_1h = float(attr.get("price_change_percentage", {}).get("h1", 0) or 0)
                chg_24h= float(attr.get("price_change_percentage", {}).get("h24", 0) or 0)
                txns_1h= int(attr.get("transactions", {}).get("h1", {}).get("buys", 0) or 0)
                liq    = float(attr.get("reserve_in_usd", 0) or 0)

                # 取得 token 合約地址
                rel = pool.get("relationships", {})
                base_token = rel.get("base_token", {}).get("data", {})
                token_id = base_token.get("id", "")  # 格式: {network}_{address}
                token_addr = token_id.split("_")[-1] if "_" in token_id else ""

                results.append({
                    "symbol":    symbol,
                    "chain":     chain,
                    "pool_addr": addr,
                    "token_addr": token_addr,
                    "price":     price,
                    "vol_1h":    vol_1h,
                    "vol_24h":   vol_24h,
                    "chg_1h":    chg_1h,
                    "chg_24h":   chg_24h,
                    "txns_1h":   txns_1h,
                    "liquidity": liq,
                })
            except Exception:
                continue

        log.info(f"[{chain}] GeckoTerminal 抓到 {len(results)} 個交易對")
        return results

    except Exception as e:
        log.error(f"[{chain}] GeckoTerminal 抓取失敗: {e}")
        return []


def fetch_new_pools(chain: str) -> list:
    """抓最新創建的交易對（新幣機會）"""
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/{chain}/new_pools"
        r = requests.get(url, timeout=10,
                        headers={"Accept": "application/json;version=20230302"})
        r.raise_for_status()
        data = r.json()
        pools = data.get("data", [])
        results = []
        for pool in pools[:20]:
            attr = pool.get("attributes", {})
            try:
                symbol  = attr.get("name", "").split("/")[0].strip()
                vol_1h  = float(attr.get("volume_usd", {}).get("h1", 0) or 0)
                chg_1h  = float(attr.get("price_change_percentage", {}).get("h1", 0) or 0)
                liq     = float(attr.get("reserve_in_usd", 0) or 0)
                price   = float(attr.get("base_token_price_usd", 0) or 0)
                created = attr.get("pool_created_at", "")
                txns_1h = int(attr.get("transactions", {}).get("h1", {}).get("buys", 0) or 0)

                rel = pool.get("relationships", {})
                base_token = rel.get("base_token", {}).get("data", {})
                token_id   = base_token.get("id", "")
                token_addr = token_id.split("_")[-1] if "_" in token_id else ""

                results.append({
                    "symbol": symbol, "chain": chain,
                    "pool_addr": attr.get("address", ""),
                    "token_addr": token_addr,
                    "price": price, "vol_1h": vol_1h,
                    "vol_24h": 0, "chg_1h": chg_1h, "chg_24h": 0,
                    "txns_1h": txns_1h, "liquidity": liq,
                    "is_new": True, "created_at": created,
                })
            except Exception:
                continue
        return results
    except Exception as e:
        log.error(f"[{chain}] 新幣池抓取失敗: {e}")
        return []

# ── 主掃描邏輯 ────────────────────────────────────────────────

def scan_once():
    log.info("═══ LANA Social Scanner 開始掃描 ═══")
    signals  = []
    scanned  = []

    for chain in CHAINS:
        tokens = fetch_trending_tokens(chain) + fetch_new_pools(chain)

        # 去重（同 symbol 只保留量最大的）
        seen = {}
        for t in tokens:
            sym = t["symbol"]
            if sym not in seen or t["vol_1h"] > seen[sym]["vol_1h"]:
                seen[sym] = t
        tokens = list(seen.values())

        for token in tokens:
            try:
                sym     = token["symbol"]
                vol_1h  = token["vol_1h"]
                chg_1h  = token["chg_1h"]
                liq     = token["liquidity"]

                # 基本過濾：成交量 + 漲幅 + 流動性
                # 暫時移除所有門檻 debug
                pass

                # 取得交易所上架狀態
                listing = get_listing_status(sym)

                # 暫時不過濾，debug 用
                # if listing["status"] == "LISTED":
                #     continue

                # AI 風險分析
                risk = analyze_token_risk(token)
                trade = {
                    "direction": risk.get("direction", "WATCH"),
                    "entry":     risk.get("entry", "N/A"),
                    "stop_loss": risk.get("stop_loss", "N/A"),
                    "target_1":  risk.get("target_1", "N/A"),
                    "target_2":  risk.get("target_2", "N/A"),
                }
                token["listing"]  = listing
                token["risk"]     = risk
                token["trade"]    = trade
                scanned.append(token)

                # 達標條件：風險分數夠高才推播
                if True:  # 暫時顯示所有訊號，debug 用
                    signals.append(token)
                    log.info(f"✅ [{chain}] {sym} 達標！{listing['strength']} 風險分:{risk.get('risk_score', risk.get('score', 0))}")

            except Exception as e:
                log.error(f"處理 {token.get('symbol','?')} 出錯: {e}")

    # 排序：未上架優先，風險分高的在前
    def sort_key(t):
        status_order = {"UNLISTED": 0, "OKX_ONLY": 1, "BNB_ONLY": 2}
        return (status_order.get(t["listing"]["status"], 3), -t["risk"].get("risk_score", t["risk"].get("score", 0)))

    signals.sort(key=sort_key)
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

    with _lock:
        _cache["signals"]     = signals
        _cache["scanned"]     = scanned
        _cache["last_update"] = now_str
        _cache["scan_count"] += 1

    if signals:
        send_signal(signals)
        log.info(f"📨 推播 {len(signals)} 個訊號")
    else:
        log.info("本輪無達標訊號")

    log.info(f"═══ 掃描完畢，共掃 {len(scanned)} 個幣 ═══\n")


def scan_smart_money():
    """聰明錢掃描（每10分鐘）"""
    log.info("═══ 聰明錢掃描 ═══")
    with _lock:
        okx = _cache["okx_listed"].copy()
        bnb = _cache["bnb_listed"].copy()
    results = scan_smart_wallets(okx, bnb)
    if results:
        send_smart_money_alert(results)
        log.info(f"🐋 推播 {len(results)} 個聰明錢訊號")

def scan_kol():
    """KOL 推文掃描（每5分鐘）"""
    log.info("═══ KOL 掃描 ═══")
    with _lock:
        okx = _cache["okx_listed"].copy()
        bnb = _cache["bnb_listed"].copy()
    results = scan_kol_mentions(okx, bnb)
    if results:
        send_kol_alert(results)
        log.info(f"🐦 推播 {len(results)} 個 KOL 訊號")

def run_scheduler():
    # 每小時刷新交易所上架清單
    schedule.every(1).hours.do(refresh_exchange_listings)
    # 每 N 分鐘掃描
    schedule.every(SCAN_INTERVAL_MIN).minutes.do(scan_once)
    schedule.every(SCAN_INTERVAL_MIN).minutes.do(scan_kol)
    schedule.every(10).minutes.do(scan_smart_money)

    log.info(f"⏰ 排程啟動，每 {SCAN_INTERVAL_MIN} 分鐘掃描")
    try:
        refresh_exchange_listings()
        log.info("✅ 交易所清單載入完成")
    except Exception as e:
        log.error(f"❌ 交易所清單載入失敗（繼續執行）: {e}")
    try:
        scan_once()
        log.info("✅ 第一次掃描完成")
    except Exception as e:
        log.error(f"❌ 第一次掃描失敗: {e}")

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    # 啟動 Telegram 頻道監控（背景）
    if os.getenv("TG_API_ID") and os.getenv("TG_API_HASH"):
        import asyncio
        def run_tg():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            async def tg_callback(sig):
                send_tg_alpha_alert(sig)
            with _lock:
                okx = _cache["okx_listed"].copy()
                bnb = _cache["bnb_listed"].copy()
            loop.run_until_complete(start_monitoring(okx, bnb, tg_callback))
        tg_thread = threading.Thread(target=run_tg, daemon=True)
        tg_thread.start()
        log.info("📡 Telegram 頻道監控啟動")
    else:
        log.info("⚠️ TG_API_ID/TG_API_HASH 未設定，跳過 TG 監控")

    log.info(f"🌐 Web API 啟動 port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
