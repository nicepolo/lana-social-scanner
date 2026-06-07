"""
ai_risk.py — Social Scanner AI 風險分析
主力：Gemini Flash（免費）
快取：1小時內不重複分析
"""

import os, logging, requests, json, time

log = logging.getLogger(__name__)

GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_cache = {}
CACHE_MIN = 60  # 社群訊號快取1小時

def analyze_risk(token_data: dict) -> dict:
    symbol = token_data.get("symbol", "?")
    now = time.time()

    # 快取
    if symbol in _cache and _cache[symbol]["expire"] > now:
        return _cache[symbol]["result"]

    prompt = f"""分析土狗幣風險。幣種:{symbol} 鏈:{token_data.get('chain','?')} 漲幅:{token_data.get('change_1h',0):+.1f}% 成交量:{token_data.get('volume_usd',0):,.0f}USD 流動性:{token_data.get('liquidity',0):,.0f}USD OKX:{token_data.get('on_okx',False)} Binance:{token_data.get('on_binance',False)}

JSON回答：{{"risk_score":0-100,"risk_level":"極高或高或中","direction":"LONG或SHORT或WATCH","entry":"進場價或區間","stop_loss":"止損","target_1":"目標1","target_2":"目標2","red_flags":["風險1","風險2"],"green_flags":["利多1"],"summary":"一句話建議"}}"""

    result = None
    if GEMINI_KEY:
        result = _gemini(prompt, symbol)
    if not result and ANTHROPIC_KEY:
        result = _claude(prompt, symbol)
    if not result:
        result = {"risk_score": 80, "risk_level": "高", "direction": "WATCH",
                  "entry": "N/A", "stop_loss": "N/A", "target_1": "N/A", "target_2": "N/A",
                  "red_flags": ["AI 無法分析"], "green_flags": [], "summary": "請謹慎操作"}

    _cache[symbol] = {"result": result, "expire": now + CACHE_MIN * 60}
    return result


def _gemini(prompt: str, symbol: str) -> dict | None:
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250}
        }, timeout=15)
        if not r.ok:
            return None
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        log.error(f"Gemini risk 失敗 {symbol}: {e}")
        return None


def _claude(prompt: str, symbol: str) -> dict | None:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 250,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=15
        )
        if not r.ok:
            return None
        text = r.json()["content"][0]["text"]
        text = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return None
