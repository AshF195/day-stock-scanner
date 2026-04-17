import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import io
import time

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Live Momentum Radar", page_icon="📈", layout="wide")

# ==========================================
# 1. IPO CALENDAR (Cached to save resources)
# ==========================================
@st.cache_data(ttl=3600)
def get_ipo_calendar():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
    }
    df = pd.DataFrame()
    
    # SOURCE 1: StockAnalysis
    try:
        url = "https://stockanalysis.com/ipos/calendar/"
        resp = requests.get(url, headers=headers, timeout=10)
        tables = pd.read_html(io.StringIO(resp.text))
        if tables:
            df = tables[0].fillna("TBD")
    except Exception:
        pass 

    # SOURCE 2: Backup - Yahoo
    if df.empty:
        try:
            url = "https://finance.yahoo.com/calendar/ipo"
            resp = requests.get(url, headers=headers, timeout=10)
            tables = pd.read_html(io.StringIO(resp.text))
            if tables:
                df = tables[0].fillna("TBD")
                useful_cols = [col for col in ['Symbol', 'Company', 'Exchange', 'Date', 'Price Range', 'Shares'] if col in df.columns]
                if useful_cols: df = df[useful_cols]
        except Exception:
            pass

    # VOLATILITY ESTIMATOR LOGIC
    if not df.empty:
        shares_col = next((col for col in df.columns if 'share' in col.lower()), None)
        if shares_col:
            potentials = []
            for val in df[shares_col]:
                val_str = str(val).replace(',', '').replace('$', '').strip().upper()
                try:
                    if 'M' in val_str: shares = float(val_str.replace('M', '').strip()) * 1000000
                    elif val_str in ['-', 'TBD', 'NAN', 'N/A']: shares = -1
                    else: shares = float(val_str)
                        
                    if shares == -1: potentials.append("❓ Unknown")
                    elif shares <= 5000000: potentials.append("🔥 Extreme (Low Float)")
                    elif shares <= 15000000: potentials.append("⭐ High")
                    else: potentials.append("📊 Standard (Heavy)")
                except ValueError:
                    potentials.append("❓ Unknown")
            
            if 'Vol Potential' not in df.columns:
                df.insert(0, 'Vol Potential', potentials)
                
    return df

# ==========================================
# 2. THE LIVE MOMENTUM ENGINE (5-Min Charts)
# ==========================================
def detect_live_momentum(ticker):
    """
    Hunts for immediate, real-time intraday breakouts using 5m candles.
    """
    try:
        # Pull the last 5 days of 5-min data to ensure we have enough volume average data
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        
        if df.empty or len(df) < 3:
            return None
            
        # Get just today's data for HOD calculations
        today_date = df.index[-1].date()
        df_today = df[df.index.date == today_date]
        
        if len(df_today) < 2:
            return None # Market just opened, not enough data today yet

        current_candle = df_today.iloc[-1]
        prev_candle = df_today.iloc[-2]
        
        # Flatten MultiIndex columns if yfinance returns them
        if isinstance(current_candle.name, tuple):
            pass # Handle based on exact yfinance version, but usually fine
            
        close_curr = float(current_candle['Close'].iloc[0] if isinstance(current_candle['Close'], pd.Series) else current_candle['Close'])
        close_prev = float(prev_candle['Close'].iloc[0] if isinstance(prev_candle['Close'], pd.Series) else prev_candle['Close'])
        vol_curr = float(current_candle['Volume'].iloc[0] if isinstance(current_candle['Volume'], pd.Series) else current_candle['Volume'])
        
        score = 0
        receipt = []
        
        # --- 1. PRICE VELOCITY (The Sudden Climb) ---
        quick_surge_pct = ((close_curr - close_prev) / close_prev) * 100
        
        if quick_surge_pct > 2.0:
            score += 10
            receipt.append(f"🚀 Violent 5m Surge (+{quick_surge_pct:.2f}%)")
        elif quick_surge_pct > 0.75:
            score += 5
            receipt.append(f"📈 Quick 5m Climb (+{quick_surge_pct:.2f}%)")
        elif quick_surge_pct < 0:
            return None # Drop it entirely if the current 5m candle is red! We only want surging stocks.
            
        # --- 2. VOLUME VELOCITY (The Gas Pedal) ---
        avg_5m_vol = df_today['Volume'].mean()
        if isinstance(avg_5m_vol, pd.Series): avg_5m_vol = float(avg_5m_vol.iloc[0])
        
        current_vol_spike = vol_curr / avg_5m_vol if avg_5m_vol > 0 else 0
        
        if current_vol_spike > 4.0:
            score += 8
            receipt.append(f"💥 Massive Vol Influx ({current_vol_spike:.1f}x normal)")
        elif current_vol_spike > 2.0:
            score += 4
            receipt.append(f"🔥 Heavy Vol Stepping In ({current_vol_spike:.1f}x)")
            
        # 🚨 GHOST TOWN PENALTY: Don't alert on zero-volume illiquid stocks
        if avg_5m_vol < 5000: # If it averages less than 5k shares per 5 mins, skip it
            return None

        # --- 3. HIGH OF DAY (HOD) PROXIMITY ---
        hod = float(df_today['High'].max().iloc[0] if isinstance(df_today['High'].max(), pd.Series) else df_today['High'].max())
        dist_to_hod = ((hod - close_curr) / hod) * 100
        
        if dist_to_hod <= 1.0:
            score += 5
            receipt.append(f"🎯 Pushing HOD (Within {dist_to_hod:.1f}%)")

        # TIERING
        if score >= 15: tier = "🔥 S-Tier (Exploding)"
        elif score >= 9: tier = "⭐ A-Tier (Surging)"
        elif score >= 5: tier = "👍 B-Tier (Waking Up)"
        else: return None # If it's below 5 points, we don't care. Keep the screen clean.

        return {
            "Ticker": ticker,
            "Tier": tier,
            "Score": score,
            "Price": f"${close_curr:.2f}",
            "5m Surge %": f"+{quick_surge_pct:.2f}%",
            "Vol Spike": f"{current_vol_spike:.1f}x",
            "Alerts": " | ".join(receipt)
        }

    except Exception as e:
        return None

# ==========================================
# 3. UI & APP LAYOUT
# ==========================================
st.title("🦅 Intraday Momentum Radar")
st.markdown("Hunts for **live 5-minute surges**, unusual volume influxes, and High-of-Day breakouts.")

tab1, tab2 = st.tabs(["🚀 Live Radar", "📅 IPO Calendar"])

with tab1:
    col1, col2 = st.columns([1, 3])
    
    with col1:
        st.subheader("Radar Settings")
        market_choice = st.selectbox("Select Market", ["US Tech / Meme Stocks (Pre-loaded)", "UK Market (LSE Pre-loaded)", "Custom Watchlist"])
        
        # Load a default pool of traditionally highly volatile day-trading stocks
        default_us = "TSLA, NVDA, AMD, MARA, PLTR, SOFI, COIN, RIOT, AMC, GME, SMCI, ALV, SMR, CVNA, MSTR"
        default_uk = "RR.L, LLOY.L, BARC.L, BP.L, SHEL.L, VOD.L, TSCO.L, EZJ.L, IAG.L"
        
        if market_choice == "Custom Watchlist":
            ticker_input = st.text_area("Paste Tickers (Comma separated):", "AAPL, MSFT")
        elif market_choice == "UK Market (LSE Pre-loaded)":
            ticker_input = st.text_area("Scanning these UK Tickers:", default_uk)
        else:
            ticker_input = st.text_area("Scanning these US Tickers:", default_us)
            
        run_scan = st.button("🚀 RUN LIVE RADAR NOW", use_container_width=True)
        
        st.info("**Pro-Tip:** Wall Street algos run on 5-minute candles. Hit this button every 5-10 minutes to see what is *just* starting to squeeze.")

    with col2:
        if run_scan:
            tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
            
            if not tickers:
                st.warning("Please enter some tickers to scan.")
            else:
                progress_text = "Scanning 5-minute charts..."
                my_bar = st.progress(0, text=progress_text)
                
                results = []
                for i, ticker in enumerate(tickers):
                    # Update progress bar
                    percent_complete = int(((i + 1) / len(tickers)) * 100)
                    my_bar.progress(percent_complete, text=f"Analyzing {ticker} ({percent_complete}%)")
                    
                    data = detect_live_momentum(ticker)
                    if data:
                        results.append(data)
                        
                my_bar.empty() # Clear progress bar when done
                
                if results:
                    # Convert to dataframe and sort by Score descending
                    res_df = pd.DataFrame(results).sort_values(by="Score", ascending=False)
                    st.success(f"Found {len(res_df)} stocks actively surging right now!")
                    st.dataframe(res_df, use_container_width=True, hide_index=True)
                else:
                    st.warning("No immediate 5-minute momentum found. The market might be choppy or resting. Try again in 5 minutes!")

with tab2:
    st.subheader("Upcoming IPO Calendar")
    st.markdown("Identifies potential low-float runners before they hit the market.")
    
    with st.spinner("Fetching IPO data..."):
        ipo_df = get_ipo_calendar()
        if not ipo_df.empty:
            st.dataframe(ipo_df, use_container_width=True, hide_index=True)
        else:
            st.error("Could not retrieve IPO calendar data at this time.")
