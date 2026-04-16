import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures

# Mobile-friendly layout
st.set_page_config(page_title="Day Trade Scanner", layout="centered", initial_sidebar_state="collapsed")

st.title("⚡ Day Trade Momentum Scanner")
st.markdown("Finds explosive stocks and warns you when they are cresting using VWAP & 5m RSI.")

# --- 1. INDEX SCRAPER (Upgraded & Robust) ---
@st.cache_data(ttl=86400)
def get_tickers(market):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
    urls = {
        "Nasdaq 100 (US)": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "S&P 500 (US)": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "S&P 400 MidCap (US)": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "S&P 600 SmallCap (US)": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        "Dow Jones (US)": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
        "FTSE 100 (UK)": "https://en.wikipedia.org/wiki/FTSE_100_Index",
        "FTSE 250 (UK)": "https://en.wikipedia.org/wiki/FTSE_250_Index"
    }
    
    if market not in urls: return []
    
    try:
        tables = pd.read_html(urls[market], storage_options=headers)
        for df in tables:
            # Dynamically find the right columns in case Wikipedia changes them
            cols = list(df.columns)
            ticker_col = next((c for c in cols if c in ['Ticker', 'Symbol', 'Ticker symbol']), None)
            name_col = next((c for c in cols if c in ['Company', 'Security', 'Company Name']), None)
            
            if ticker_col and name_col:
                raw_tickers = df[ticker_col].astype(str).tolist()
                names = df[name_col].astype(str).tolist()
                
                clean_tickers = []
                for t in raw_tickers:
                    if "FTSE" not in market:
                        t = t.replace(".", "-") # Fixes BRK.B to BRK-B for Yahoo Finance
                    else:
                        t = t + ".L" # Appends .L for London Stock Exchange
                    clean_tickers.append(t)
                    
                # Return a list of tuples: [('AAPL', 'Apple Inc.'), ('NVDA', 'Nvidia')]
                return list(zip(clean_tickers, names))
                
    except Exception as e:
        st.error(f"Error scraping Wikipedia for {market}: {e}")
        
    return []

# --- 2. SCORING & CRESTING LOGIC ---
def analyze_day_trading_metrics(ticker_info):
    ticker, company_name = ticker_info
    try:
        stock = yf.Ticker(ticker)
        
        # TIER 1: Daily Data for Gaps, RVOL, and ADR
        df_daily = stock.history(period="1mo")
        if len(df_daily) < 15: return None
            
        prev_close = df_daily['Close'].iloc[-2]
        today_open = df_daily['Open'].iloc[-1]
        today_vol = df_daily['Volume'].iloc[-1]
        
        avg_vol = df_daily['Volume'].iloc[-15:-1].mean()
        if avg_vol < 100000: return None # Filter out illiquid stocks
        
        rvol = today_vol / avg_vol if avg_vol > 0 else 0
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        
        df_daily['Daily_Range'] = (df_daily['High'] - df_daily['Low']) / df_daily['Close'].shift(1)
        adr = df_daily['Daily_Range'].iloc[-15:-1].mean() * 100
        
        # Filter: Only proceed if stock has a catalyst, high RVOL, or good ADR
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
        
        # Calculate Distance from VWAP
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

        # --- CRESTING LOGIC ---
        crest_status = "🟢 Room to Run"
        if vwap_dist > 6.0 and current_rsi > 75:
            crest_status = "🚨 EXTREME PEAK (Reversal)"
        elif vwap_dist > 4.0 or current_rsi > 75:
            crest_status = "⚠️ Cresting"
        elif vwap_dist < -4.0:
            crest_status = "⚠️ Bottoming"

        return {
            "Ticker": ticker,
            "Company": company_name,
            "Status": crest_status,
            "Score": score,
            "Price": round(current_price, 2),
            "Chg %": round(day_change, 2),
            "Gap %": round(gap_pct, 2),
            "RVOL": round(rvol, 2),
            "VWAP Dist %": round(vwap_dist, 2),
            "5m RSI": round(current_rsi, 2)
        }
    except Exception:
        return None

# --- 3. UI & EXECUTION ---
market_choices = [
    "Nasdaq 100 (US)", "S&P 500 (US)", "S&P 400 MidCap (US)", "S&P 600 SmallCap (US)", 
    "Dow Jones (US)", "FTSE 100 (UK)", "FTSE 250 (UK)", "Manual"
]
market_choice = st.selectbox("🌍 Select Market:", market_choices)

manual_tickers = ""
if market_choice == "Manual":
    manual_tickers = st.text_input("Enter tickers (comma separated):", "TSLA, NVDA, AAPL")

if st.button("🚀 Scan Market", type="primary", use_container_width=True):
    if market_choice == "Manual":
        ticker_list = [(t.strip().upper(), "Manual Entry") for t in manual_tickers.split(",") if t.strip()]
    else:
        ticker_list = get_tickers(market_choice)
        
    if ticker_list:
        st.info(f"Scanning {len(ticker_list)} stocks... (Filtering dead stocks, fetching 5m VWAP)")
        progress_bar = st.progress(0)
        
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(analyze_day_trading_metrics, t_info): t_info for t_info in ticker_list}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                progress_bar.progress((i + 1) / len(ticker_list))
                res = future.result()
                if res: results.append(res)
                    
        if results:
            df = pd.DataFrame(results).sort_values(by="Score", ascending=False)
            
            # Row coloring for extreme peaks
            def row_style(row):
                if "EXTREME PEAK" in str(row['Status']): return ['background-color: #4a0000; color: white'] * len(row)
                if "Cresting" in str(row['Status']): return ['background-color: #4a3b00; color: white'] * len(row)
                return [''] * len(row)

            # Strict 2-decimal formatting applied directly to the UI
            st.dataframe(
                df.style.apply(row_style, axis=1),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                    "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
                    "Gap %": st.column_config.NumberColumn("Gap %", format="%.2f%%"),
                    "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                    "VWAP Dist %": st.column_config.NumberColumn("VWAP Dist %", format="%.2f%%"),
                    "5m RSI": st.column_config.NumberColumn("5m RSI", format="%.2f"),
                }
            )
        else:
            st.warning("No stocks met the day trading volatility and volume criteria right now.")
    else:
        st.error("No tickers found or could not connect to index data.")
