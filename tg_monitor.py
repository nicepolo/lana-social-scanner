"""
tg_monitor.py — Telegram Alpha 頻道監控
用 Telethon 監控指定頻道，偵測新幣提及
不需要 X API，完全免費
"""

import os, re, logging, asyncio, json
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

TG_API_ID   = os.getenv("TG_API_ID", "")
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION  = os.getenv("TG_SESSION", "lana_monitor")  # session 名稱

TZ_TAIPEI = timezone(timedelta(hours=8))

# ── 監控頻道清單 ──────────────────────────────────────────────
ALPHA_CHANNELS = [
    # SOL 鏈 Alpha
    {"channel": "solana_degens",       "label": "SOL Degens",      "chain": "solana"},
    {"channel": "pumpfun_alpha",       "label": "PumpFun Alpha",   "chain": "solana"},
    {"channel": "sol_meme_calls",      "label": "SOL Meme Calls",  "chain": "solana"},
    {"channel": "solana_apes_official","label": "Solana Apes",     "chain": "solana"},
    {"channel": "gmgn_ai",             "label": "GMGN AI",         "chain": "solana"},
    # BSC 鏈 Alpha
    {"channel": "bnb_chain_gems",      "label": "BNB Gems",        "chain": "bsc"},
    {"channel": "bscgems",             "label": "BSC Gems",        "chain": "bsc"},
    {"channel": "dextools_trending",   "label": "DexTools 趨勢",   "chain": "bsc"},
    # 中文圈
    {"channel": "crypto_cn_alpha",     "label": "中文Alpha",       "chain": "mixed"},
    {"channel": "binance_wallet_news", "label": "幣安錢包資訊",    "chain": "mixed"},
    # 鏈上分析
    {"channel": "lookonchain",         "label": "LookOnChain",     "chain": "mixed"},
    {"channel": "onchainlens",         "label": "OnChainLens",     "chain": "mixed"},
    {"channel": "spotonchain",         "label": "SpotOnChain",     "chain": "mixed"},
]

# 幣名識別（$XXXX 或合約地址）
TOKEN_PATTERN   = re.compile(r'\$([A-Z]{2,10})\b')
SOL_ADDR_PATTERN= re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')  # Solana 地址
BSC_ADDR_PATTERN= re.compile(r'\b(0x[a-fA-F0-9]{40})\b')              # BSC 地址

EXCLUDE_WORDS = {
    "BTC", "ETH", "SOL", "BNB", "USDT", "USDC", "USD", "NFT",
    "AI", "GM", "GN", "LFG", "WAGMI", "DAO", "CEX", "DEX",
    "THE", "AND", "FOR", "NOT", "BUT", "ALL", "NEW", "TOP",
}

# 儲存最近訊號避免重複推播
_recent_signals = set()  # (channel, symbol, hour)


async def start_monitoring(okx_listed: set, bnb_listed: set, callback):
    """
    啟動 Telethon 監控
    callback: async function(signal_dict) — 有新訊號時呼叫
    """
    if not TG_API_ID or not TG_API_HASH:
        log.warning("TG_API_ID / TG_API_HASH 未設定，跳過 Telegram 監控")
        return

    try:
        from telethon import TelegramClient, events
        from telethon.tl.types import Channel

        client = TelegramClient(TG_SESSION, int(TG_API_ID), TG_API_HASH)
        await client.start()
        log.info("✅ Telethon 客戶端啟動成功")

        # 取得所有頻道的實體
        channel_entities = {}
        for ch_config in ALPHA_CHANNELS:
            try:
                entity = await client.get_entity(ch_config["channel"])
                channel_entities[entity.id] = ch_config
                log.info(f"✅ 已訂閱頻道: {ch_config['label']}")
            except Exception as e:
                log.warning(f"無法訂閱 {ch_config['channel']}: {e}")

        @client.on(events.NewMessage(chats=list(channel_entities.keys())))
        async def handler(event):
            try:
                ch_config = channel_entities.get(event.chat_id, {})
                text = event.message.text or ""
                if not text:
                    return

                signals = _parse_message(
                    text=text,
                    channel_label=ch_config.get("label", "?"),
                    channel_name=ch_config.get("channel", "?"),
                    chain=ch_config.get("chain", "mixed"),
                    okx_listed=okx_listed,
                    bnb_listed=bnb_listed,
                )
                for sig in signals:
                    await callback(sig)

            except Exception as e:
                log.error(f"訊息處理出錯: {e}")

        log.info("📡 開始監控 Telegram 頻道...")
        await client.run_until_disconnected()

    except ImportError:
        log.error("請安裝 telethon: pip install telethon")
    except Exception as e:
        log.error(f"Telethon 啟動失敗: {e}")


def _parse_message(text: str, channel_label: str, channel_name: str,
                   chain: str, okx_listed: set, bnb_listed: set) -> list:
    """解析訊息，提取幣名和合約地址"""
    results = []
    now_hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")

    # 1. 找 $SYMBOL 格式
    symbols = TOKEN_PATTERN.findall(text.upper())
    for sym in symbols:
        if sym in EXCLUDE_WORDS or len(sym) < 2:
            continue
        key = (channel_name, sym, now_hour)
        if key in _recent_signals:
            continue
        _recent_signals.add(key)
        # 清理舊記錄（只保留最近 200 筆）
        if len(_recent_signals) > 200:
            _recent_signals.pop()

        on_okx = sym in okx_listed
        on_bnb = sym in bnb_listed
        if on_okx and on_bnb:
            continue  # 已上架，不值得追

        results.append({
            "type":          "tg_mention",
            "source":        "telegram",
            "channel_label": channel_label,
            "channel_name":  channel_name,
            "symbol":        sym,
            "token_addr":    "",
            "chain":         chain,
            "message_text":  text[:200],
            "on_okx":        on_okx,
            "on_binance":    on_bnb,
            "strength":      "🔥🔥🔥 超強" if not on_okx and not on_bnb else "🔥🔥 強",
            "timestamp":     datetime.now(timezone.utc).timestamp(),
        })

    # 2. 找合約地址
    sol_addrs = SOL_ADDR_PATTERN.findall(text)
    bsc_addrs = BSC_ADDR_PATTERN.findall(text)

    for addr in sol_addrs[:2]:
        key = (channel_name, addr, now_hour)
        if key in _recent_signals:
            continue
        _recent_signals.add(key)
        results.append({
            "type":          "tg_contract",
            "source":        "telegram",
            "channel_label": channel_label,
            "channel_name":  channel_name,
            "symbol":        addr[:8] + "...",
            "token_addr":    addr,
            "chain":         "solana",
            "message_text":  text[:200],
            "on_okx":        False,
            "on_binance":    False,
            "strength":      "🔥🔥🔥 超強",
            "timestamp":     datetime.now(timezone.utc).timestamp(),
        })

    for addr in bsc_addrs[:2]:
        key = (channel_name, addr, now_hour)
        if key in _recent_signals:
            continue
        _recent_signals.add(key)
        results.append({
            "type":          "tg_contract",
            "source":        "telegram",
            "channel_label": channel_label,
            "channel_name":  channel_name,
            "symbol":        addr[:8] + "...",
            "token_addr":    addr,
            "chain":         "bsc",
            "message_text":  text[:200],
            "on_okx":        False,
            "on_binance":    False,
            "strength":      "🔥🔥🔥 超強",
            "timestamp":     datetime.now(timezone.utc).timestamp(),
        })

    return results


def get_channel_list() -> list:
    return ALPHA_CHANNELS.copy()
