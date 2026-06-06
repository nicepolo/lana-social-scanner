"""
kol_monitor.py — X (Twitter) KOL 監控
監控知名加密 KOL 的推文，偵測新幣提及
免費方案：用 Nitter RSS（不需要 X API Key）
付費方案：用 X API v2
"""

import os, logging, requests, re, json
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
TZ_TAIPEI      = timezone(timedelta(hours=8))

# ── KOL 監控名單 ────────────────────────────────────────────
# 加密圈重要 KOL，有發新幣名稱就追蹤
KOL_LIST = [
    # 中文圈
    {"username": "heyibinance",   "label": "何一 (幣安)",      "lang": "zh"},
    {"username": "cz_binance",    "label": "CZ (幣安)",        "lang": "en"},
    {"username": "justinsuntron", "label": "孫宇晨",            "lang": "zh"},
    # 英文圈 SOL Meme 大 V
    {"username": "blknoiz06",     "label": "Ansem",            "lang": "en"},
    {"username": "inversebrah",   "label": "InverseBrah",      "lang": "en"},
    {"username": "msolana",       "label": "mSOLana",          "lang": "en"},
    {"username": "notthreadguy",  "label": "ThreadGuy",        "lang": "en"},
    # 鏈上分析
    {"username": "lookonchain",   "label": "LookOnChain",      "lang": "en"},
    {"username": "onchainlens",   "label": "OnChainLens",      "lang": "en"},
    {"username": "spotonchain",   "label": "SpotOnChain",      "lang": "en"},
]

# Nitter 公共實例（免費，不需要 X API）
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

# 幣名辨識正則（$XXXX 格式，或 #XXX 格式）
TOKEN_PATTERN = re.compile(r'\$([A-Z]{2,10})\b|#([A-Z]{2,10})\b')

# 排除常見非幣名詞
EXCLUDE_WORDS = {
    "BTC", "ETH", "SOL", "BNB", "USDT", "USDC", "USD", "NFT", "DeFi",
    "AI", "APY", "TVL", "ATH", "ATL", "GM", "GN", "LFG", "WAGMI",
    "DAO", "CEX", "DEX", "RPC", "API", "SDK", "IDO", "IEO", "ICO",
}


def scan_kol_mentions(okx_listed: set, bnb_listed: set) -> list:
    """掃描 KOL 推文，找出提及未上架新幣的推文"""
    results = []

    for kol in KOL_LIST:
        try:
            if X_BEARER_TOKEN:
                tweets = _get_tweets_api(kol["username"])
            else:
                tweets = _get_tweets_nitter(kol["username"])

            for tweet in tweets:
                tokens = _extract_tokens(tweet["text"])
                for token in tokens:
                    if token in EXCLUDE_WORDS:
                        continue
                    on_okx = token in okx_listed
                    on_bnb = token in bnb_listed
                    # 只推未上架的幣
                    if on_okx and on_bnb:
                        continue

                    results.append({
                        "type":        "kol_mention",
                        "kol_username": kol["username"],
                        "kol_label":   kol["label"],
                        "symbol":      token,
                        "tweet_text":  tweet["text"][:200],
                        "tweet_url":   tweet.get("url", ""),
                        "timestamp":   tweet.get("timestamp", 0),
                        "on_okx":      on_okx,
                        "on_binance":  on_bnb,
                        "strength":    "🔥🔥🔥 超強" if not on_okx and not on_bnb else "🔥🔥 強",
                    })
                    log.info(f"🐦 [{kol['label']}] 提及 ${token} — 未上架!")

        except Exception as e:
            log.error(f"KOL {kol['username']} 掃描失敗: {e}")

    return results


def _get_tweets_nitter(username: str) -> list:
    """用 Nitter RSS 免費抓推文（不需要 X API）"""
    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance}/{username}/rss"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                continue

            root = ET.fromstring(r.text)
            channel = root.find("channel")
            if channel is None:
                continue

            results = []
            now_ts = datetime.now(timezone.utc).timestamp()

            for item in channel.findall("item")[:10]:
                title   = item.findtext("title", "")
                desc    = item.findtext("description", "")
                link    = item.findtext("link", "")
                pub_date= item.findtext("pubDate", "")

                text = f"{title} {desc}"
                # 清除 HTML 標籤
                text = re.sub(r'<[^>]+>', ' ', text)

                # 解析時間
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    ts = dt.timestamp()
                except:
                    ts = now_ts

                # 只看1小時內的推文
                if (now_ts - ts) > 3600:
                    continue

                results.append({
                    "text":      text,
                    "url":       link,
                    "timestamp": ts,
                })

            return results

        except Exception as e:
            log.warning(f"Nitter {instance} 失敗: {e}")
            continue

    return []


def _get_tweets_api(username: str) -> list:
    """用 X API v2（需要付費 Bearer Token）"""
    try:
        # 先取 user_id
        url = f"https://api.twitter.com/2/users/by/username/{username}"
        headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
        r = requests.get(url, headers=headers, timeout=10)
        if not r.ok:
            return []
        user_id = r.json()["data"]["id"]

        # 取最近推文
        url2 = f"https://api.twitter.com/2/users/{user_id}/tweets"
        params = {
            "max_results": 10,
            "tweet.fields": "created_at,text",
            "exclude": "retweets,replies"
        }
        r2 = requests.get(url2, headers=headers, params=params, timeout=10)
        if not r2.ok:
            return []

        now_ts = datetime.now(timezone.utc).timestamp()
        results = []
        for tweet in r2.json().get("data", []):
            try:
                from datetime import datetime
                dt = datetime.strptime(tweet["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
                ts = dt.replace(tzinfo=timezone.utc).timestamp()
            except:
                ts = now_ts

            if (now_ts - ts) > 3600:
                continue

            results.append({
                "text":      tweet["text"],
                "url":       f"https://x.com/{username}/status/{tweet['id']}",
                "timestamp": ts,
            })

        return results

    except Exception as e:
        log.error(f"X API 失敗 {username}: {e}")
        return []


def _extract_tokens(text: str) -> list:
    """從推文中提取幣名"""
    matches = TOKEN_PATTERN.findall(text.upper())
    tokens = set()
    for m in matches:
        token = m[0] or m[1]
        if token and len(token) >= 2:
            tokens.add(token)
    return list(tokens)


def get_kol_list() -> list:
    return KOL_LIST.copy()
