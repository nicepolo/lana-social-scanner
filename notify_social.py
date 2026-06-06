"""
notify_social.py — LANA Social Scanner Telegram 推播
土狗幣情緒交易訊號格式
"""

import os, logging, requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TZ_TAIPEI = timezone(timedelta(hours=8))

CHAIN_EMOJI = {"bsc": "🟡 BSC", "solana": "🟣 SOL"}
CHAIN_EXPLORER = {
    "bsc":    "https://bscscan.com/token/",
    "solana": "https://solscan.io/token/",
}
CHAIN_DEX = {
    "bsc":    "https://pancakeswap.finance/swap?outputCurrency=",
    "solana": "https://raydium.io/swap/?inputCurrency=sol&outputCurrency=",
}

def send_signal(signals: list):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram 憑證未設定，跳過推播")
        return

    now = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")
    header = (
        f"🚨 *LANA 土狗情緒訊號* | {now}\n"
        f"偵測到 {len(signals)} 個機會\n"
        f"{'─' * 26}"
    )
    _send(header)

    for i, t in enumerate(signals[:5], 1):  # 最多推5個
        msg = _format_signal(i, t)
        _send(msg)

    footer = (
        f"{'─' * 26}\n"
        "⚠️ *風險提示*\n"
        "土狗幣極度高風險，隨時歸零。\n"
        "務必先查合約、看持倉分布，\n"
        "每筆不超過總資金 1-3%。"
    )
    _send(footer)


def _format_signal(idx: int, t: dict) -> str:
    sym    = t.get("symbol", "?")
    chain  = t.get("chain", "bsc")
    price  = t.get("price", 0)
    vol_1h = t.get("vol_1h", 0)
    chg_1h = t.get("chg_1h", 0)
    liq    = t.get("liquidity", 0)
    txns   = t.get("txns_1h", 0)
    is_new = t.get("is_new", False)
    token_addr = t.get("token_addr", "")

    listing = t.get("listing", {})
    risk    = t.get("risk", {})

    strength = listing.get("strength", "")
    on_okx   = "✅ 已上架" if listing.get("on_okx") else "❌ 未上架"
    on_bnb   = "✅ 已上架" if listing.get("on_binance") else "❌ 未上架"

    verdict    = risk.get("verdict", "")
    risk_level = risk.get("risk_level", "")
    score      = risk.get("score", 0)
    summary    = risk.get("summary", "")
    reason     = risk.get("reason", "")
    action     = risk.get("suggested_action", "")
    max_pos    = risk.get("max_position_pct", 1)
    red_flags  = risk.get("red_flags", [])
    green_flags= risk.get("green_flags", [])

    chain_name = CHAIN_EMOJI.get(chain, chain.upper())
    explorer   = CHAIN_EXPLORER.get(chain, "") + token_addr if token_addr else "N/A"
    dex_link   = CHAIN_DEX.get(chain, "") + token_addr if token_addr else "N/A"

    chg_str = f"+{chg_1h:.1f}%" if chg_1h >= 0 else f"{chg_1h:.1f}%"
    new_tag = " 🆕新幣" if is_new else ""

    score_emoji = "🔥" if score >= 70 else "⚡" if score >= 50 else "⚠️"

    lines = [
        f"{score_emoji} *#{idx} {sym}*{new_tag} | {chain_name}",
        f"現價：`${price:.8g}` | 1H {chg_str}",
        f"成交量1H：`${vol_1h/1000:.1f}K` | 流動性：`${liq/1000:.1f}K`",
        f"買入筆數1H：{txns} 筆",
        "",
        f"📊 交叉比對",
        f"OKX：{on_okx}　Binance：{on_bnb}",
        f"訊號強度：{strength}",
        "",
        f"🤖 AI 風險評估：{score}/100 {risk_level}",
        f"結論：*{verdict}*",
        f"_{summary}_",
        f"{reason}",
    ]

    if green_flags:
        lines.append("")
        lines.append("✅ " + " | ".join(green_flags[:2]))

    if red_flags:
        lines.append("❌ " + " | ".join(red_flags[:2]))

    lines += [
        "",
        f"💡 *建議操作*：{action}",
        f"最大倉位建議：{max_pos}%",
        "",
        f"🔍 [查合約]({explorer}) | [去DEX買入]({dex_link})",
    ]

    return "\n".join(lines)


def _send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":                  CHAT_ID,
                "text":                     text,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10
        )
        if not r.ok:
            log.error(f"Telegram 發送失敗: {r.text[:100]}")
    except Exception as e:
        log.error(f"Telegram 請求異常: {e}")
