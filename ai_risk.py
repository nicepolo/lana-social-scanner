"""
ai_risk.py — 用 Claude AI 分析土狗幣風險
輸入：token 資訊 + 交易所上架狀態
輸出：風險評分 + 建議
"""

import os, json, logging, requests

log = logging.getLogger(__name__)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

CHAIN_EXPLORER = {
    "bsc":    "https://bscscan.com/token/",
    "solana": "https://solscan.io/token/",
}

def analyze_token_risk(token: dict, listing: dict) -> dict:
    """AI 分析風險，回傳結構化結果"""
    if not ANTHROPIC_API_KEY:
        return _rule_based_risk(token, listing)

    sym     = token.get("symbol", "?")
    chain   = token.get("chain", "?")
    vol_1h  = token.get("vol_1h", 0)
    vol_24h = token.get("vol_24h", 0)
    chg_1h  = token.get("chg_1h", 0)
    chg_24h = token.get("chg_24h", 0)
    liq     = token.get("liquidity", 0)
    txns    = token.get("txns_1h", 0)
    price   = token.get("price", 0)
    is_new  = token.get("is_new", False)
    token_addr = token.get("token_addr", "N/A")
    explorer = CHAIN_EXPLORER.get(chain, "") + token_addr if token_addr else "N/A"

    on_okx = listing.get("on_okx", False)
    on_bnb = listing.get("on_binance", False)
    strength = listing.get("strength", "")

    prompt = f"""你是一位專業的土狗幣（Meme Coin）風險分析師，擅長識別貔貅盤、騙局和真實機會。

請分析以下鏈上新幣的風險和機會：

幣種：{sym}
鏈別：{chain.upper()}
合約：{token_addr}
瀏覽器：{explorer}
現價：${price}

【交易數據】
1小時成交量：${vol_1h:,.0f}
24小時成交量：${vol_24h:,.0f}
1小時漲幅：{chg_1h:.1f}%
24小時漲幅：{chg_24h:.1f}%
流動性池：${liq:,.0f}
1小時買入筆數：{txns}
是否新幣：{"是，剛創建" if is_new else "否"}

【交易所上架狀態】
OKX：{"已上架" if on_okx else "未上架"}
Binance：{"已上架" if on_bnb else "未上架"}
訊號強度：{strength}

請輸出以下 JSON（只輸出 JSON，不要其他文字）：
{{
  "score": 整數0-100（越高越值得關注，考慮機會和風險的平衡）,
  "risk_level": "低風險" | "中風險" | "高風險" | "極高風險",
  "opportunity": "強" | "中" | "弱",
  "verdict": "建議關注" | "謹慎觀察" | "高風險勿碰",
  "summary": "一句話總結（繁體中文20字內）",
  "reason": "分析理由（繁體中文60字內）",
  "red_flags": ["風險點1", "風險點2"],
  "green_flags": ["機會點1", "機會點2"],
  "suggested_action": "建議操作（繁體中文30字內）",
  "max_position_pct": 建議最大倉位百分比（整數，1-5）
}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": MODEL,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content.strip())
        return result
    except Exception as e:
        log.error(f"AI 風險分析失敗 {sym}: {e}")
        return _rule_based_risk(token, listing)


def _rule_based_risk(token: dict, listing: dict) -> dict:
    """備用規則式風險分析"""
    score = 50
    red_flags = []
    green_flags = []

    vol_1h = token.get("vol_1h", 0)
    chg_1h = token.get("chg_1h", 0)
    liq    = token.get("liquidity", 0)
    txns   = token.get("txns_1h", 0)
    is_new = token.get("is_new", False)
    status = listing.get("status", "LISTED")

    # 加分項
    if status == "UNLISTED":
        score += 20
        green_flags.append("兩大交易所均未上架，資訊落差大")
    elif status in ["OKX_ONLY", "BNB_ONLY"]:
        score += 10
        green_flags.append("僅一家上架，仍有上架套利空間")

    if vol_1h > 500000:
        score += 10
        green_flags.append(f"1h成交量 ${vol_1h/1000:.0f}K，熱度高")
    elif vol_1h > 100000:
        score += 5
        green_flags.append(f"1h成交量 ${vol_1h/1000:.0f}K，有熱度")

    if txns > 200:
        score += 5
        green_flags.append(f"1h買入 {txns} 筆，散戶積極")

    # 扣分項
    if liq < 50000:
        score -= 15
        red_flags.append(f"流動性池僅 ${liq/1000:.0f}K，滑點大")

    if chg_1h > 100:
        score -= 10
        red_flags.append(f"1h已漲 {chg_1h:.0f}%，追高風險大")

    if is_new:
        score -= 10
        red_flags.append("新幣，合約風險未知")

    score = max(0, min(100, score))

    if score >= 70:
        verdict = "建議關注"
        risk_level = "中風險"
        opportunity = "強"
    elif score >= 50:
        verdict = "謹慎觀察"
        risk_level = "高風險"
        opportunity = "中"
    else:
        verdict = "高風險勿碰"
        risk_level = "極高風險"
        opportunity = "弱"

    return {
        "score":           score,
        "risk_level":      risk_level,
        "opportunity":     opportunity,
        "verdict":         verdict,
        "summary":         "，".join(green_flags[:1]) if green_flags else "無明確機會",
        "reason":          " | ".join((green_flags + red_flags)[:3]),
        "red_flags":       red_flags,
        "green_flags":     green_flags,
        "suggested_action": "小倉試水，嚴設止損" if score >= 50 else "暫不操作",
        "max_position_pct": 3 if score >= 70 else 1
    }
