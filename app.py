from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import aiosqlite
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import httpx
import asyncio
import logging
import time
import base64
import websockets
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("valshi")

load_dotenv()

# Get API key from environment
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")

# Path for private key
KALSHI_PRIVATE_KEY_PATH = "keys/kalshi_private.pem"

# Check if private key is in environment variable, write it to file
kalshi_key_content = os.getenv("KALSHI_PRIVATE_KEY", "")
if kalshi_key_content:
    os.makedirs("keys", exist_ok=True)
    with open(KALSHI_PRIVATE_KEY_PATH, "w") as f:
        f.write(kalshi_key_content)
    log.info("✅ Private key loaded from environment variable")

DB_PATH = "valshi.db"

app = FastAPI(title="Valshi")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ... rest of your code stays the same


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("valshi")

load_dotenv()
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_PRIVATE_KEY_PATH = "keys/kalshi_private.pem"
DB_PATH = "valshi.db"

app = FastAPI(title="Valshi")
app.mount("/static", StaticFiles(directory="static"), name="static")

class KalshiClient:
    def __init__(self, host="https://api.elections.kalshi.com"):
        self.host = host.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    
    async def get(self, path, params=None):
        url = f"{self.host}{path}"
        try:
            r = await self.client.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"API error: {e}")
            return {}

KALSHI = KalshiClient()

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prints(
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                side TEXT,
                price REAL,
                count INTEGER,
                notional_usd REAL,
                ts_ms INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS market_cache(
                ticker TEXT PRIMARY KEY,
                title TEXT,
                tags TEXT,
                fetched_at INTEGER
            )
        """)
        await db.commit()

def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)

async def get_market_info(ticker):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, tags FROM market_cache WHERE ticker=?", (ticker,))
        row = await cur.fetchone()
    
    if row:
        title, tags_str = row
        return title, tags_str.split(",") if tags_str else []
    
    try:
        data = await KALSHI.get(f"/trade-api/v2/markets/{ticker}/")
        market = data.get("market", {})
        title = market.get("title") or market.get("subtitle", ticker)
        tags = market.get("tags") or []
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO market_cache(ticker, title, tags, fetched_at) VALUES(?,?,?,?)",
                (ticker, title, ",".join(tags), now_ms())
            )
            await db.commit()
        return title, tags
    except:
        return ticker, []

@app.get("/")
async def home():
    return FileResponse("templates/index.html")

@app.get("/api/recent-trades")
async def recent_trades(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ticker, side, notional_usd, ts_ms FROM prints ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        trades = await cur.fetchall()
    
    result = []
    for t in trades:
        title, _ = await get_market_info(t[0])
        result.append({
            "ticker": t[0],
            "title": title,
            "side": t[1],
            "notional": t[2],
            "timestamp": t[3]
        })
    return {"trades": result}

@app.get("/api/top-trades")
async def top_trades(hours: int = 24):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT ticker, side, notional_usd, ts_ms FROM prints ORDER BY notional_usd DESC LIMIT 10"
        )
        trades = await cur.fetchall()
    
    result = []
    for t in trades:
        title, _ = await get_market_info(t[0])
        result.append({
            "ticker": t[0],
            "title": title,
            "side": t[1],
            "notional": t[2],
            "timestamp": t[3]
        })
    return {"trades": result}

@app.get("/api/stats")
async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT SUM(notional_usd), COUNT(*), COUNT(DISTINCT ticker) FROM prints")
        row = await cur.fetchone()
    return {"total_volume": row[0] or 0, "trade_count": row[1] or 0, "unique_markets": row[2] or 0}

@app.get("/api/search-markets")
async def search_markets(q: str = ""):
    if not q or len(q) < 2:
        return {"markets": []}
    
    try:
        data = await KALSHI.get("/v1/search/series", params={
            "query": q,
            "embedding_search": "true",
            "order_by": "querymatch",
            "limit": 20
        })
        
        series_list = data.get("current_page", [])
        
        results = []
        for s in series_list[:10]:
            for market in s.get("markets", []):
                results.append({
                    "ticker": market.get("ticker"),
                    "title": s.get("series_title"),
                    "yes_price": market.get("yes_price", 0) / 100,
                    "volume": s.get("total_series_volume", 0)
                })
        
        return {"markets": results}
    except Exception as e:
        log.error(f"Search error: {e}")
        return {"markets": []}

@app.get("/api/leaderboard")
async def get_leaderboard(metric: str = "volume", timeframe: int = 0):
    try:
        data = await KALSHI.get("/v1/social/leaderboard", params={
            "metric_name": metric,
            "limit": 10,
            "since_day_before": timeframe
        })
        
        rank_list = data.get("rank_list", [])
        
        results = []
        for i, trader in enumerate(rank_list, 1):
            results.append({
                "rank": i,
                "nickname": trader.get("nickname", "Unknown"),
                "value": trader.get("value", 0)
            })
        
        return {"leaderboard": results}
    except Exception as e:
        log.error(f"Leaderboard error: {e}")
        return {"leaderboard": []}

@app.websocket("/ws")
async def ws(websocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
    except:
        pass

@app.on_event("startup")
async def startup():
    await db_init()
    print("\n✅ VALSHI WEB APP http://localhost:8000\n")
    asyncio.create_task(kalshi_websocket())

async def kalshi_websocket():
    await asyncio.sleep(1)
    
    if not KALSHI_API_KEY or not os.path.exists(KALSHI_PRIVATE_KEY_PATH):
        log.warning("No API key or private key")
        return
    
    try:
        with open(KALSHI_PRIVATE_KEY_PATH, "rb") as f:
            pk = serialization.load_pem_private_key(f.read(), password=None)
    except:
        return
    
    def sign_msg(text):
        message = text.encode("utf-8")
        signature = pk.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode("utf-8")
    
    def headers(method="GET", path="/trade-api/ws/v2"):
        timestamp = str(int(time.time() * 1000))
        msg = timestamp + method + path
        signature = sign_msg(msg)
        return {
            "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp
        }
    
    url = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    delay = 1
    
    while True:
        try:
            log.info("Connecting to Kalshi...")
            async with websockets.connect(url, additional_headers=headers()) as ws:
                log.info("✅ Connected to Kalshi WebSocket")
                await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {"channels": ["trade"]}}))
                delay = 1
                
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "trade":
                            trade = data.get("msg", {})
                            ticker = trade.get("market_ticker", "")
                            side = trade.get("taker_side", "")
                            price = trade.get("yes_price", 0) / 100
                            count = trade.get("count", 0)
                            notional = count * (price if side == "yes" else (1 - price))
                            ts = int(trade.get("ts", 0) * 1000)
                            
                            if notional >= 500:
                                async with aiosqlite.connect(DB_PATH) as db:
                                    await db.execute(
                                        "INSERT INTO prints VALUES(NULL,?,?,?,?,?,?)",
                                        (ticker, side, price, count, notional, ts)
                                    )
                                    await db.commit()
                                
                                log.info(f"📊 {ticker} {side.upper()} ${notional:,.0f}")
                    except:
                        pass
        except Exception as e:
            log.error(f"WS Error: {e}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
