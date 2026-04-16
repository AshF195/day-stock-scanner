import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures

# Mobile-friendly layout
st.set_page_config(page_title="Day Trade Scanner", layout="centered", initial_sidebar_state="collapsed")

st.title("⚡ Day Trade Momentum Scanner")

# --- UI EXPLANATIONS ---
with st.expander("📖 Scanner Cheat Sheet (How to read this)"):
    st.markdown("""
    **The Grading Tiers:**
    * **🔥 S-Tier:** The absolute best setups. High volume, huge gaps, massive volatility.
    * **⭐ A-Tier:** Great setups with strong momentum.
    * **👍 B-Tier:** Acceptable setups, but might lack extreme volume or volatility.
    
    **Dynamic Scoring Metrics:**
    * **RVOL:** 1 point awarded per 0.5x increase in Relative Volume.
    * **Gap %:** 1 point awarded per 1% of gap (up or down).
    * **ADR:** 1 point awarded per 1% of Average Daily Range (starting at 1%).
    * **Float Size:** +1 point for under 60M shares, scaling up +1 point for every 5M shares lower.
    * **Short %:** +1 point for over 5% short interest, scaling up +1 point for every 5% higher.
    """)

# --- 1. INDEX SCRAPER ---
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
            cols = list(df.columns)
            ticker_col = next((c for c in cols if c in ['Ticker', 'Symbol', 'Ticker symbol']), None)
            name_col = next((c for c in cols if c in ['Company', 'Security', 'Company Name']), None)
            
            if ticker_col and name_col:
                raw_tickers = df[ticker_col].astype(str).tolist()
                names = df[name_col].astype(str).tolist()
                
                clean_tickers = []
                for t in raw_tickers:
                    if "FTSE" not in market:
                        t = t.replace(".", "-")
                    else:
                        t = t + ".L"
                    clean_tickers.append(t)
                return list(zip(clean_tickers, names))
    except Exception:
        pass
    return []

# --- 2. SCORING LOGIC ---
def analyze_day_trading_metrics(ticker_info):
    ticker, company_name = ticker_info
    try:
        stock = yf.Ticker(ticker)
        
        # --- PHASE 1: Basic Technicals (Fast) ---
        df_daily = stock.history(period="1mo")
        if len(df_daily) < 15: return None
            
        prev_close = df_daily['Close'].iloc[-2]
        today_open = df_daily['Open'].iloc[-1]
        today_vol = df_daily['Volume'].iloc[-1]
        avg_vol = df_daily['Volume'].iloc[-15:-1].mean()
        
        if avg_vol < 100000: return None # Filter illiquid
        
        rvol = today_vol / avg_vol if avg_vol > 0 else 0
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        
        df_daily['Daily_Range'] = (df_daily['High'] - df_daily['Low']) / df_daily['Close'].shift(1)
        adr = df_daily['Daily_Range'].iloc[-15:-1].mean() * 100
        
        # Kill dead stocks immediately to save time (Must be dead in ALL 3 metrics to drop)
        if rvol < 1.0 and abs(gap_pct) < 1.0 and adr < 1.5: return None

        # --- PHASE 2: Intraday VWAP & RSI ---
        df_intra = stock.history(period="1d", interval="5m")
        if df_intra.empty or len(df_intra) < 14: return None
        
        current_price = df_intra['Close'].iloc[-1]
        day_change = ((current_price - prev_close) / prev_close) * 100
        
        df_intra['Typical_Price'] = (df_intra['High'] + df_intra['Low'] + df_intra['Close']) / 3
        df_intra['VWAP'] = (df_intra['Typical_Price'] * df_intra['Volume']).cumsum() / df_intra['Volume'].cumsum()
        current_vwap = df_intra['VWAP'].iloc[-1]
        vwap_dist = ((current_price - current_vwap) / current_vwap) * 100
        
        delta = df_intra['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi_5m = 100 - (100 / (1 + (gain / loss)))
        current_rsi = rsi_5m.iloc[-1]

        # Calculate Base Score (Dynamic Math Scaling)
        score = 0
        
        # 1. Gap Scoring (1 point per 1% gap)
        score += int(abs(gap_pct))
        
        # 2. RVOL Scoring (1 point per 0.5x RVOL)
        if rvol > 0:
            score += int(rvol / 0.5)
            
        # 3. ADR Scoring (1 point per 1% ADR, starting at 1%)
        if adr >= 1.0:
            score += int(adr)
        
        if score < 3: return None

        # --- PHASE 3: Deep Analytics (Only for surviving stocks) ---
        info = stock.info
        float_shares = info.get('floatShares', 0)
        short_pct = info.get('shortPercentOfFloat', 0)
        
        float_display = "N/A"
        # 4. Float Size Scoring (< 60M = 1pt, scaling +1 for every 5M lower)
        if float_shares > 0:
            float_display = f"{float_shares / 1000000:.1f}M"
            if float_shares < 60000000:
                float_m = float_shares / 1000000
                float_points = int((60 - float_m) / 5) + 1
                score += float_points
            
        short_display = "N/A"
        # 5. Short Interest Scoring (> 5% = 1pt, scaling +1 for every 5% higher)
        if short_pct is not None and short_pct > 0:
            short_val = short_pct * 100
            short_display = f"{short_val:.1f}%"
            if short_val >= 5.0:
                short_points = int(short_val / 5)
                score += short_points

        # --- TIERING & CRESTING ---
        if score >= 15: tier = "🔥 S-Tier"
        elif score >= 10: tier = "⭐ A-Tier"
        elif score >= 5: tier = "👍 B-Tier"
        else: tier = "C-Tier"

        crest_status = "🟢 Run"
        if vwap_dist > 6.0 and current_rsi > 75: crest_status = "🚨 PEAK"
        elif vwap_dist > 4.0 or current_rsi > 75: crest_status = "⚠️ Cresting"

        return {
            "Tier": tier,
            "Score": score,
            "Ticker": ticker,
            "Status": crest_status,
            "Price": round(current_price, 2),
            "Chg %": round(day_change, 2),
            "Gap %": round(gap_pct, 2),
            "RVOL": round(rvol, 2),
            "Float": float_display,
            "Short %": short_display,
            "VWAP Dist %": round(vwap_dist, 2),
            "Company": company_name
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
    ticker_list = [(t.strip().upper(), "Manual Entry") for t in manual_tickers.split(",") if t.strip()] if market_choice == "Manual" else get_tickers(market_choice)
        
    if ticker_list:
        st.info(f"Scanning {len(ticker_list)} stocks... (Running Dynamic Scoring Engine)")
        progress_bar = st.progress(0)
        
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(analyze_day_trading_metrics, t_info): t_info for t_info in ticker_list}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                progress_bar.progress((i + 1) / len(ticker_list))
                res = future.result()
                if res: results.append(res)
                    
        if results:
            df = pd.DataFrame(results).sort_values(by="Score", ascending=False)
            
            st.divider()
            st.subheader("🏆 Top 10 High-Conviction Setups")
            
            top_10_df = df.head(10).drop(columns=['Company']) 
            
            def row_style(row):
                if "PEAK" in str(row['Status']): return ['background-color: #4a0000; color: white'] * len(row)
                if "Cresting" in str(row['Status']): return ['background-color: #4a3b00; color: white'] * len(row)
                return [''] * len(row)

            st.dataframe(
                top_10_df.style.apply(row_style, axis=1),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                    "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
                    "Gap %": st.column_config.NumberColumn("Gap %", format="%.2f%%"),
                    "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                    "VWAP Dist %": st.column_config.NumberColumn("VWAP Dist %", format="%.2f%%"),
                }
            )
            
            if len(df) > 10:
                with st.expander(f"View Remaining {len(df) - 10} Active Stocks"):
                    st.dataframe(df.iloc[10:].drop(columns=['Company']).style.apply(row_style, axis=1), hide_index=True, use_container_width=True)
                    
        else:
            st.warning("No stocks met the required volatility, volume, or momentum criteria right now.")
    else:
        st.error("No tickers found or could not connect to index data.")
