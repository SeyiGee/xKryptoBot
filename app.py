from fastapi import FastAPI, Request
import os
import aiohttp
import ccxt.async_support as ccxt
import pandas as pd
import pandas_ta as ta

app = FastAPI()
TOKEN = "8963718222:AAG0DAZH1oJvIy8LeXAW3Zcu5vZuIBq3qXE"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

async def send_telegram_message(chat_id: int, text: str):
    async with aiohttp.ClientSession() as session:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        async with session.post(TELEGRAM_API_URL, json=payload) as response:
            return await response.json()

@app.post("/")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "ok"}
    
    if "message" not in data or "text" not in data["message"]:
        return {"status": "ok"}
    
    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message["text"].strip()
    
    if text.startswith("/analyze"):
        args = text.split()
        if len(args) < 3:
            await send_telegram_message(
                chat_id,
                "⚠️ *Usage Format:*\n"
                "`/analyze <symbol_or_contract> <portfolio_usd> [leverage]`\n\n"
                "• *CEX Example:* `/analyze BTC 10000 10`\n"
                "• *DEX Example:* `/analyze SOL 5000`"
            )
            return {"status": "ok"}
        
        query = args[1]
        try:
            portfolio_size = float(args[2])
        except ValueError:
            await send_telegram_message(chat_id, "⚠️ Portfolio size must be a valid number.")
            return {"status": "ok"}
            
        leverage = int(args[3]) if len(args) > 3 else 1
        
        market_data = None
        is_perp = True
        
        # Check CEX first via CCXT
        try:
            await exchange.load_markets()
            formatted_symbol = f"{query.upper()}/USDT:USDT"
            if formatted_symbol in exchange.markets:
                ticker = await exchange.fetch_ticker(formatted_symbol)
                ohlcv = await exchange.fetch_ohlcv(formatted_symbol, timeframe='1h', limit=50)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['rsi'] = ta.rsi(df['close'], length=14)
                current_rsi = round(float(df['rsi'].iloc[-1]), 2)
                
                market_data = {
                    "type": "CEX_PERP",
                    "symbol": query.upper(),
                    "price": float(ticker['last']),
                    "volume_24h": float(ticker['quoteVolume']),
                    "change_24h": float(ticker['percentage']),
                    "rsi_1h": current_rsi,
                    "venue": "CEX (Binance Futures)"
                }
        except Exception as e:
            print(f"CEX error: {e}")
            
        # Fallback to DEX (DexScreener) if not found on CEX
        if not market_data:
            is_perp = False
            leverage = 1
            url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        res_json = await response.json()
                        pairs = res_json.get("pairs", [])
                        if pairs:
                            best = max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0)))
                            market_data = {
                                "type": "DEX",
                                "symbol": best.get("baseToken", {}).get("symbol"),
                                "price": float(best.get("priceUsd", 0)),
                                "liquidity": float(best.get("liquidity", {}).get("usd", 0)),
                                "volume_24h": float(best.get("volume", {}).get("h24", 0)),
                                "change_24h": float(best.get("priceChange", {}).get("h24", 0)),
                                "venue": f"DEX ({best.get('dexId', 'DEX').upper()})"
                            }
                            
        if not market_data:
            await send_telegram_message(chat_id, f"❌ Could not find market data for `{query}` on CEX or DEX networks.")
            return {"status": "ok"}
            
        price = market_data["price"]
        buffer = 0.02 if is_perp else 0.04
        entry = price
        stop_loss = round(entry * (1 - buffer), 6)
        tp1 = round(entry * (1 + (buffer * 1.5)), 6)
        tp2 = round(entry * (1 + (buffer * 3.0)), 6)
        
        risk_pct = 1.5
        risk_amount = portfolio_size * (risk_pct / 100.0)
        price_risk = abs(entry - stop_loss)
        position_tokens = risk_amount / price_risk if price_risk > 0 else 0
        notional_value = position_tokens * entry
        required_margin = notional_value / leverage if is_perp else notional_value
        
        response_text = (
            f"🚀 *INSTITUTIONAL TRADE SETUP: {market_data['symbol']}*\n"
            f"🌐 *Venue:* `{market_data['venue']}` | 💵 *Price:* `${price:,.4f}`\n\n"
            f"📊 *Market Health:*\n"
            f"• *24h Volume:* `${market_data['volume_24h']:,.0f}`\n"
            f"• *24h Change:* `{market_data['change_24h']}%`\n"
        )
        
        if is_perp:
            response_text += f"• *1H RSI:* `{market_data['rsi_1h']}`\n\n"
        else:
            response_text += f"• *Liquidity:* `${market_data['liquidity']:,.0f}`\n\n"
            
        response_text += (
            f"🎯 *Optimized Execution Plan:*\n"
            f"• *Direction:* `LONG (Bullish Confluence)`\n"
            f"• *Entry Zone:* `${entry:,.4f}`\n"
            f"• *Stop Loss:* `${stop_loss:,.4f}` 🛑\n"
            f"• *Take Profit 1:* `${tp1:,.4f}` (1:1.5 R:R)\n"
            f"• *Take Profit 2:* `${tp2:,.4f}` (1:3.0 R:R)\n\n"
            f"⚖️ *Portfolio Risk Sizing (${portfolio_size:,.0f} Portfolio):*\n"
            f"• *Target Risk (1.5%):* `${risk_amount:,.2f}`\n"
            f"• *Position Size:* `{round(position_tokens, 4)} {market_data['symbol']}`\n"
            f"• *Notional Value:* `${round(notional_value, 2)}`\n"
            f"• *Required Margin ({leverage}x):* `${round(required_margin, 2)}`"
        )
        
        await send_telegram_message(chat_id, response_text)
        
    elif text.startswith("/start"):
        await send_telegram_message(
            chat_id,
            "⚡ *Crypto Alpha Bot Live!*\n\n"
            "Send commands directly in chat:\n"
            "`/analyze BTC 10000 10` (CEX Futures)\n"
            "`/analyze SOL 5000` (DEX Spot)"
        )
        
    return {"status": "ok"}
          
