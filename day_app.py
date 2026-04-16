import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures
import requests
import time

# --- MOBILE-FRIENDLY LAYOUT ---
st.set_page_config(page_title="Day Trade Scanner", layout="centered", initial_sidebar_state="collapsed")

# --- SESSION STATE INITIALIZATION ---
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []
if "scan_results" not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

st.title("⚡ Day Trade Momentum Scanner")

with st.expander("📖 Scanner Cheat Sheet & Status Explanations"):
    st.markdown("""
    **The Grading Tiers:**
    * **🔥 S-Tier:** The absolute best setups. High volume, huge gaps, massive volatility.
    * **⭐ A-Tier:** Great setups with strong momentum.
    * **👍 B-Tier:** Acceptable setups, but might lack extreme volume or volatility.
    
    **Status Indicators (The VWAP Rubber Band):**
    * 🟢 **Run:** Active momentum, volume is supporting the price.
    * ⚠️ **Cresting:** Overbought, price is outpacing volume. Getting stretched from the VWAP.
    * 🚨 **PEAK:** Extremely overbought and dangerous. High probability of a sudden dump.
    * 📉 **Under VWAP:** Stock is losing the intraday battle to sellers. Momentum dying.

    **Dynamic Scoring Metrics:**
    * **RVOL:** 1 point per 0.5x increase in Relative Volume.
    * **Gap %:** 1 point per 1% of gap (up or down).
    * **ADR:** 1 point per 1% of Average Daily Range (starting at 1%).
    * **Float Size:** +1 point for under 60M shares, scaling up +1 for every 5M lower.
    * **Short %:** +1 point for over 5% short interest, scaling up +1 for every 5% higher.
    """)

# --- WEB SESSION SETUP ---
@st.cache_resource
def get_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    })
    # Inject a consent cookie to bypass Yahoo's EU/UK privacy blocker popups!
    session.cookies.set('CONSENT', 'YES+cb', domain='.yahoo.com')
    return session

yf_session = get_session()

# --- 1A. LIVE GAINERS SCRAPER (NEVER CACHED) ---
def get_live_gainers(market):
    live_urls = {
        "US Day Gainers (Yahoo Live)": "https://finance.yahoo.com/screener/predefined/day_gainers",
        "US Pre-Market Gainers (Yahoo Live)": "https://finance.yahoo.com/screener/predefined/premarket_gainers",
        "UK Day Gainers (Yahoo Live)": "https://uk.finance.yahoo.com/screener/predefined/day_gainers"
    }
    try:
        import io
        resp = yf_session.get(live_urls[market], timeout=10)
        tables = pd.read_html(io.StringIO(resp.text))
        
        if tables:
            df = tables[0]
            # Drop empty rows to prevent crashes
            df = df.dropna(subset=['Symbol'])
            raw_tickers = df['Symbol'].astype(str).tolist()
            
            # Strip out hidden graphical junk Yahoo puts in the symbol column (e.g. "A AXTI" -> "AXTI")
            clean_tickers = [t.split()[-1].upper() for t in raw_tickers if t.strip()]
            names = df['Name'].astype(str).tolist() if 'Name' in df.columns else [""] * len(clean_tickers)
            
            return list(zip(clean_tickers, names))
            
    except ValueError:
        st.error("⚠️ Yahoo blocked the scraper (No tables found). Wait 1 minute and try again.")
    except Exception as e:
        st.error(f"⚠️ Live Scraper Error: {e}")
    return []

# --- 1B. STATIC INDEX SCRAPER (CACHED FOR 24 HRS) ---
@st.cache_data(ttl=86400) 
def get_static_tickers(market):
    headers = {'User-Agent': 'Mozilla/5.0'}
    urls = {
        "Nasdaq 100 (US)": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "S&P 500 (US)": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "FTSE 100 (UK)": "https://en.wikipedia.org/wiki/FTSE_100_Index",
        "FTSE 250 (UK)": "https://en.wikipedia.org/wiki/FTSE_250_Index"
    }
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
                    t = t.replace(".", "-") 
                    if "FTSE" in market:
                        t = t + ".L"
                    clean_tickers.append(t)
                return list(zip(clean_tickers, names))
    except Exception:
        pass
    return []

# --- 2. SCORING LOGIC ---
def analyze_day_trading_metrics(ticker_info, is_tracking=False):
    ticker, company_name = ticker_info
    
    # Pause to prevent API rate limits
    time.sleep(0.5) 
    
    try:
        stock = yf.Ticker(ticker)
        df_daily = stock.history(period="1mo")
        
        if df_daily.empty: 
            raise Exception("Yahoo returned empty data")
            
        if len(df_daily) < 15: return None
            
        prev_close = df_daily['Close'].iloc[-2]
        today_open = df_daily['Open'].iloc[-1]
        today_vol = df_daily['Volume'].iloc[-1]
        avg_vol = df_daily['Volume'].iloc[-15:-1].mean()
        
        if avg_vol < 100000 and not is_tracking: return None 
        
        rvol = today_vol / avg_vol if avg_vol > 0 else 0
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        
        df_daily['Daily_Range'] = (df_daily['High'] - df_daily['Low']) / df_daily['Close'].shift(1)
        adr = df_daily['Daily_Range'].iloc[-15:-1].mean() * 100
        
        if rvol < 1.0 and abs(gap_pct) < 1.0 and adr < 1.5 and not is_tracking: return None

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

        score = 0
        score += int(abs(gap_pct))
        if rvol > 0: score += int(rvol / 0.5)
        if adr >= 1.0: score += int(adr)
        
        if score < 3 and not is_tracking: return None

        # Deep Analytics (Protected for missing UK Data)
        float_display = "N/A"
        short_display = "N/A"
        try:
            info = stock.info or {} 
            float_shares = info.get('floatShares', 0)
            short_pct = info.get('shortPercentOfFloat', 0)
            
            if float_shares and float_shares > 0:
                float_display = f"{float_shares / 1000000:.1f}M"
                if float_shares < 60000000: score += int((60 - (float_shares / 1000000)) / 5) + 1
                
            if short_pct and short_pct > 0:
                short_val = short_pct * 100
                short_display = f"{short_val:.1f}%"
                if short_val >= 5.0: score += int(short_val / 5)
        except Exception:
            pass 

        if score >= 15: tier = "🔥 S-Tier"
        elif score >= 10: tier = "⭐ A-Tier"
        elif score >= 5: tier = "👍 B-Tier"
        else: tier = "C-Tier"

        crest_status = "🟢 Run"
        if vwap_dist > 6.0 and current_rsi > 75: crest_status = "🚨 PEAK"
        elif vwap_dist > 4.0 or current_rsi > 75: crest_status = "⚠️ Cresting"
        elif current_price < current_vwap: crest_status = "📉 Under VWAP"

        return {
            "Ticker": ticker,
            "Company": company_name, 
            "Tier": tier,
            "Score": score,
            "Status": crest_status,
            "Price": round(current_price, 2),
            "Chg %": round(day_change, 2),
            "Gap %": round(gap_pct, 2),
            "RVOL": round(rvol, 2),
            "VWAP Dist %": round(vwap_dist, 2),
            "Float": float_display,
            "Short %": short_display
        }
    except Exception as e:
        raise Exception(f"API Error - {str(e)}")

# --- UI STYLING FUNCTION (RAG Text Formatting) ---
def color_status(val):
    val_str = str(val)
    if "PEAK" in val_str: return 'color: #FF3333; font-weight: bold'  
    if "Cresting" in val_str: return 'color: #FFaa00; font-weight: bold'  
    if "Run" in val_str: return 'color: #33CC33; font-weight: bold'  
    if "Under VWAP" in val_str: return 'color: #CC0000'                     
    return ''

# --- TOP SECTION: LIVE WATCHLIST ---
if st.session_state.watchlist:
    st.success("🎯 **Active Watchlist** (Pinned)")
    
    if st.button("🔄 Refresh Watchlist Prices", type="primary"):
        with st.spinner("Fetching live data..."):
            tracked_results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(analyze_day_trading_metrics, (t, "Watchlist"), True): t for t in st.session_state.watchlist}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        res = future.result()
                        if res: tracked_results.append(res)
                    except Exception:
                        pass
            
            if tracked_results:
                df_tracked = pd.DataFrame(tracked_results).sort_values(by="Score", ascending=False)
                
                sell_warnings = df_tracked[df_tracked['Status'].isin(["🚨 PEAK", "⚠️ Cresting"])]['Ticker'].tolist()
                if sell_warnings:
                    st.error(f"**⚠️ SELL WARNING:** Momentum is overextended on: **{', '.join(sell_warnings)}**. Consider taking profits!")
                
                st.dataframe(
                    df_tracked.style.map(color_status, subset=["Status"]),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Status": st.column_config.TextColumn("Status", help="🟢 Run: Active momentum.\n⚠️ Cresting: Overbought, getting stretched.\n🚨 PEAK: Extremely overbought, dump imminent.\n📉 Under VWAP: Momentum dying."),
                        "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                        "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
                        "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx")
                    }
                )
    st.divider()

# --- BOTTOM SECTION: MARKET SCANNER ---
market_choices = [
    "UK Day Gainers (Yahoo Live)", "US Day Gainers (Yahoo Live)", "US Pre-Market Gainers (Yahoo Live)",
    "Nasdaq 100 (US)", "S&P 500 (US)", "FTSE 100 (UK)", "FTSE 250 (UK)", "Manual"
]
market_choice = st.selectbox("🌍 Select Market to Scan:", market_choices)

manual_tickers = ""
if market_choice == "Manual":
    manual_tickers = st.text_input("Enter tickers (comma separated):", "TSLA, NVDA, AAPL")

if st.button("🚀 Scan Market", use_container_width=True):
    
    # Check which scraping method to use
    if market_choice == "Manual":
        ticker_list = [(t.strip().upper(), "Manual") for t in manual_tickers.split(",") if t.strip()]
    elif "Yahoo Live" in market_choice:
        ticker_list = get_live_gainers(market_choice) # Bypasses cache entirely
    else:
        ticker_list = get_static_tickers(market_choice)
        
    if ticker_list:
        st.info(f"Scanning {len(ticker_list)} stocks...")
        progress_bar = st.progress(0)
        results = []
        errors = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(analyze_day_trading_metrics, t_info): t_info for t_info in ticker_list}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                progress_bar.progress((i + 1) / len(ticker_list))
                try:
                    res = future.result()
                    if res: results.append(res)
                except Exception as e:
                    errors.append(f"{futures[future][0]}: {str(e)}") 
                    
        if errors and len(errors) > 5:
            st.error(f"⚠️ Caught {len(errors)} API timeouts. Yahoo may be rate-limiting.")
            with st.expander("View Error Logs"):
                st.write(errors)

        if results:
            st.session_state.scan_results = pd.DataFrame(results).sort_values(by="Score", ascending=False)
        else:
            st.warning("No stocks met the criteria right now.")
            st.session_state.scan_results = pd.DataFrame()
    else:
        # Added explicit warning if the scraper fails to find anything entirely
        st.warning("⚠️ Scraper returned zero stocks. The market might be closed, or the index list is temporarily down.")

# Render the Scan Results Interactive Table
if not st.session_state.scan_results.empty:
    st.subheader("🏆 Scanner Results")
    st.caption("Tick the box on the left to pin a stock to your Live Watchlist at the top.")
    
    df_display = st.session_state.scan_results.copy()
    
    if 'Track' not in df_display.columns:
        df_display.insert(0, "Track", df_display["Ticker"].isin(st.session_state.watchlist))
    else:
        df_display["Track"] = df_display["Ticker"].isin(st.session_state.watchlist)
    
    edited_df = st.data_editor(
        df_display.style.map(color_status, subset=["Status"]),
        hide_index=True,
        use_container_width=True,
        disabled=[col for col in df_display.columns if col not in ["Track"]], 
        column_config={
            "Track": st.column_config.CheckboxColumn("📌 Track"),
            "Status": st.column_config.TextColumn("Status", help="🟢 Run: Active momentum.\n⚠️ Cresting: Overbought, getting stretched.\n🚨 PEAK: Extremely overbought, dump imminent.\n📉 Under VWAP: Momentum dying."),
            "Price": st.column_config.NumberColumn("Price", format="%.2f"),
            "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
            "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx")
        }
    )
    
    new_watchlist = edited_df[edited_df["Track"] == True]["Ticker"].tolist()
    if set(new_watchlist) != set(st.session_state.watchlist):
        st.session_state.watchlist = new_watchlist
        st.rerun()
