"""
ai_risk.py v1.1 — 風險分析 + 交易建議（做多/做空/觀望）
"""

import os, json, logging, requests
log = logging.getLogger(__name__)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

def analyze_token_risk(token: dict, listing: dict) -> tuple:
    """回傳 (risk_dict, trade_dict)"""
    if not ANTHROPIC_API_KEY:
        return _rule_based(token, listing)

    sym     = token.get("symbol", "?")
    chain   = token.get("chain", "?")
    price   = token.get("price", 0)
    vol_1h  = token.get("vol_1h", 0)
    vol_24h = token.get("vol_24h", 0)
    chg_1h  = token.get("chg_1h", 0)
    chg_24h = token.get("chg_24h", 0)
    liq     = token.get("liquidity", 0)
    txns    = token.get("txns_1h", 0)
    is_new  = token.get("is_new", False)
    token_addr = token.get("token_addr", "N/A")
    on_okx  = listing.get("on_okx", False)
    on_bnb  = listing.get("on_binance", False)
    strength= listing.get("strength", "")

    prompt = f"""你是專業土狗幣（Meme Coin）交易分析師，擅長識別風險和短線機會。

幣種：{sym} | 鏈：{chain.upper()} | 合約：{token_addr}
現價：${price:.8g}
1H 成交量：${vol_1h:,.0f} | 24H：${vol_24h:,.0f}
1H 漲幅：{chg_1h:.1f}% | 24H：{chg_24h:.1f}%
流動性池：${liq:,.0f} | 1H 買入筆數：{txns}
是否新幣：{"是" if is_new else "否"}
OKX：{"已上架" if on_okx else "未上架"} | Binance：{"已上架" if on_bnb else "未上架"}
訊號強度：{strength}

請輸出 JSON（只輸出 JSON，不要其他文字）：
{{
  "risk": {{
    "score": 整數0-100,
    "risk_level": "低風險"|"中風險"|"高風險"|"極高風險",
    "verdict": "建議關注"|"謹慎觀察"|"高風險勿碰",
    "summary": "20字內繁體中文總結",
    "reason": "60字內繁體中文理由",
    "red_flags": ["風險1", "風險2"],
    "green_flags": ["機會1", "機會2"],
    "suggested_action": "30字內建議操作",
    "max_position_pct": 倉位建議整數1-5
  }},
  "trade": {{
    "direction": "LONG"|"SHORT"|"WATCH",
    "entry": "建議進場價格或區間（例：$0.00512-$0.00520）",
    "stop_loss": "止損價格（例：$0.00485）",
    "target1": "第一目標價（例：$0.00580）",
    "target2": "第二目標價（例：$0.00650）",
    "hold_time": "預計持倉時間（例：1-4小時）",
    "entry_note": "進場條件說明（例：等量能回升再進，不追高）"
  }}
}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 800,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=25
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content.strip())
        return data.get("risk", {}), data.get("trade", {})
    except Exception as e:
        log.error(f"AI 分析失敗 {sym}: {e}")
        return _rule_based(token, listing)


def _rule_based(token: dict, listing: dict) -> tuple:
    vol_1h  = token.get("vol_1h", 0)
    chg_1h  = token.get("chg_1h", 0)
    liq     = token.get("liquidity", 0)
    txns    = token.get("txns_1h", 0)
    is_new  = token.get("is_new", False)
    price   = token.get("price", 0)
    status  = listing.get("status", "LISTED")

    score = 50
    red_flags, green_flags = [], []

    if status == "UNLISTED":
        score += 20
        green_flags.append("兩大交易所均未上架")
    elif status in ["OKX_ONLY", "BNB_ONLY"]:
        score += 10
        green_flags.append("仍有上架套利空間")

    if vol_1h > 500000:
        score += 10
        green_flags.append(f"1H成交量${vol_1h/1000:.0f}K熱度高")
    if txns > 500:
        score += 5
        green_flags.append(f"1H買入{txns}筆積極")
    if liq < 50000:
        score -= 15
        red_flags.append(f"流動性池僅${liq/1000:.0f}K")
    if chg_1h > 80:
        score -= 10
        red_flags.append(f"1H已漲{chg_1h:.0f}%追高風險")
    if is_new:
        score -= 10
        red_flags.append("新幣合約風險未知")

    score = max(0, min(100, score))

    # 交易方向
    if score >= 65 and chg_1h < 50:
        direction = "LONG"
        entry = f"${price:.6g} - ${price*1.02:.6g}"
        stop_loss = f"${price*0.85:.6g}"
        target1 = f"${price*1.30:.6g}"
        target2 = f"${price*1.60:.6g}"
        entry_note = "確認量能持續放大後進場"
    elif chg_1h > 80:
        direction = "WATCH"
        entry = "等回調再看"
        stop_loss = "N/A"
        target1 = "N/A"
        target2 = "N/A"
        entry_note = "已大漲，等回調布林中軌再評估"
    else:
        direction = "WATCH"
        entry = "等訊號確認"
        stop_loss = "N/A"
        target1 = "N/A"
        target2 = "N/A"
        entry_note = "暫無明確方向，觀望"

    risk = {
        "score": score,
        "risk_level": "中風險" if score >= 60 else "高風險",
        "verdict": "建議關注" if score >= 65 else "謹慎觀察",
        "summary": "，".join(green_flags[:1]) or "無明確機會",
        "reason": " | ".join((green_flags + red_flags)[:3]),
        "red_flags": red_flags,
        "green_flags": green_flags,
        "suggested_action": "小倉試水嚴設止損" if score >= 60 else "暫不操作",
        "max_position_pct": 2 if score >= 70 else 1
    }
    trade = {
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "hold_time": "1-4小時",
        "entry_note": entry_note
    }
    return risk, trade
