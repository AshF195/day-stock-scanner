import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures

# Mobile-friendly layout
st.set_page_config(page_title="Day Trade Scanner", layout="centered", initial_sidebar_state="collapsed")

st.title("⚡ Day Trade Momentum Scanner")
st.markdown("Finds explosive stocks and warns you when they are cresting using VWAP & 5m RSI.")

# --- 1. INDEX SCRAPER ---
@st.cache_data(ttl=86400)
def get_tickers(market):
    if market == "Nasdaq 100 (US)":
        return pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]['Ticker'].tolist()
    elif market == "S&P 500 (US)":
        return pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]['Symbol'].tolist()
    elif market == "Dow Jones (US)":
        return pd.read_html("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average")[1]['Symbol'].tolist()
    elif market == "FTSE 100 (UK)":
        return [t + ".L" for t in pd.read_html("https://en.wikipedia.org/wiki/FTSE_100_Index")[4]['Ticker'].tolist()]
    return []

# --- 2. SCORING & CRESTING LOGIC ---
def analyze_day_trading_metrics(ticker):
    try:
        stock = yf.Ticker(ticker)
        
        # TIER 1: Daily Data for Gaps, RVOL, and ADR
        df_daily = stock.history(period="1mo")
        if len(df_daily) < 15: return None
            
        prev_close = df_daily['Close'].iloc[-2]
        today_open = df_daily['Open'].iloc[-1]
        today_vol = df_daily['Volume'].iloc[-1]
        
        avg_vol = df_daily['Volume'].iloc[-15:-1].mean()
        if avg_vol < 100000: return None # Filter out illiquid stocks immediately
        
        rvol = today_vol / avg_vol if avg_vol > 0 else 0
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        
        df_daily['Daily_Range'] = (df_daily['High'] - df_daily['Low']) / df_daily['Close'].shift(1)
        adr = df_daily['Daily_Range'].iloc[-15:-1].mean() * 100
        
        # Filter: Only proceed to Tier 2 if stock has a catalyst, high RVOL, or good ADR
        if rvol < 1.2 and abs(gap_pct) < 1.5 and adr < 2.5:
            return None

        # TIER 2: Intraday 5-Minute Data for VWAP and Cresting
        df_intra = stock.history(period="1d", interval="5m")
        if df_intra.empty or len(df_intra) < 14: return None
        
        current_price = df_intra['Close'].iloc[-1]
        day_change = ((current_price - prev_close) / prev_close) * 100
        
        # Calculate VWAP
        df_intra['Typical_Price'] = (df_intra['High'] + df_intra['Low'] + df_intra['Close']) / 3
        df_intra['VWAP'] = (df_intra['Typical_Price'] * df_intra['Volume']).cumsum() / df_intra['Volume'].cumsum()
        current_vwap = df_intra['VWAP'].iloc[-1]
        
        # Calculate Distance from VWAP (The Rubber Band Measure)
        vwap_dist = ((current_price - current_vwap) / current_vwap) * 100
        
        # Calculate 5-Minute RSI
        delta = df_intra['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi_5m = 100 - (100 / (1 + rs))
        current_rsi = rsi_5m.iloc[-1]

        # Calculate Score
        score = 0
        if abs(gap_pct) >= 2.0: score += int(abs(gap_pct))
        if rvol > 1.5: score += int(rvol * 3)
        if adr > 3.0: score += int(adr)
        
        if score < 3: return None

        # --- CRESTING LOGIC (Is the stock peaking?) ---
        crest_status = "🟢 Room to Run"
        if vwap_dist > 6.0 and current_rsi > 75:
            crest_status = "🚨 EXTREME PEAK (Reversal Imminent)"
        elif vwap_dist > 4.0 or current_rsi > 75:
            crest_status = "⚠️ Cresting (Overextended)"
        elif vwap_dist < -4.0:
            crest_status = "⚠️ Bottoming (Oversold)"

        return {
            "Ticker": ticker,
            "Score": score,
            "Status": crest_status,
            "Price": round(current_price, 2),
            "Chg %": round(day_change, 2),
            "Gap %": round(gap_pct, 2),
            "RVOL": round(rvol, 2),
            "VWAP Dist %": round(vwap_dist, 2),
            "5m RSI": round(current_rsi, 1)
        }
    except Exception:
        return None

# --- 3. UI & EXECUTION ---
market_choice = st.selectbox("🌍 Select Market:", ["Nasdaq 100 (US)", "S&P 500 (US)", "Dow Jones (US)", "FTSE 100 (UK)", "Manual"])

manual_tickers = ""
if market_choice == "Manual":
    manual_tickers = st.text_input("Enter tickers (comma separated):", "TSLA, NVDA, AAPL")

if st.button("🚀 Scan Market", type="primary", use_container_width=True):
    tickers = [t.strip().upper() for t in manual_tickers.split(",") if t.strip()] if market_choice == "Manual" else get_tickers(market_choice)
        
    if tickers:
        st.info(f"Scanning {len(tickers)} stocks... (Filtering dead stocks, fetching 5m VWAP)")
        progress_bar = st.progress(0)
        
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(analyze_day_trading_metrics, t): t for t in tickers}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                progress_bar.progress((i + 1) / len(tickers))
                res = future.result()
                if res: results.append(res)
                    
        if results:
            df = pd.DataFrame(results).sort_values(by="Score", ascending=False)
            
            # Styling for Mobile App View
            def row_style(row):
                if "EXTREME PEAK" in str(row['Status']): return ['background-color: #4a0000; color: white'] * len(row)
                if "Cresting" in str(row['Status']): return ['background-color: #4a3b00; color: white'] * len(row)
                return [''] * len(row)

            st.dataframe(
                df.style.apply(row_style, axis=1),
                hide_index=True,
                use_container_width=True
            )
        else:
            st.warning("No stocks met the day trading criteria right now.")