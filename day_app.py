import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures
import requests
import time

# ==========================================
# 🛠️ YOUR CUSTOM GITHUB LISTS GO HERE 🛠️
# ==========================================
CUSTOM_LISTS = {
    "FTSE SmallCap (GitHub)": "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/ftse_smallcap.txt",
}
# ==========================================

# --- MOBILE-FRIENDLY LAYOUT ---
st.set_page_config(page_title="Day Trade Scanner", layout="centered", initial_sidebar_state="collapsed")

# --- SESSION STATE INITIALIZATION ---
if "watchlist" not in st.session_state or isinstance(st.session_state.watchlist, list):
    st.session_state.watchlist = {} 
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
    session.cookies.set('CONSENT', 'YES+cb', domain='.yahoo.com')
    return session

yf_session = get_session()

# --- 1A. LIVE GAINERS SCRAPER ---
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
            df = df.dropna(subset=['Symbol'])
            raw_tickers = df['Symbol'].astype(str).tolist()
            clean_tickers = [t.split()[-1].upper() for t in raw_tickers if t.strip()]
            names = df['Name'].astype(str).tolist() if 'Name' in df.columns else [""] * len(clean_tickers)
            return list(zip(clean_tickers, names))
    except ValueError:
        st.error("⚠️ Yahoo blocked the scraper (No tables found). Wait 1 minute and try again.")
    except Exception as e:
        st.error(f"⚠️ Live Scraper Error: {e}")
    return []

# --- 1B. STATIC INDEX SCRAPER ---
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

# --- 1C. IPO CALENDAR SCRAPER ---
@st.cache_data(ttl=3600) # Caches the list for 1 hour
def get_ipo_calendar():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
    }
    
    df = pd.DataFrame()
    
    # SOURCE 1: Try StockAnalysis (Highly reliable, clean table)
    try:
        url = "https://stockanalysis.com/ipos/calendar/"
        resp = requests.get(url, headers=headers, timeout=10)
        import io
        tables = pd.read_html(io.StringIO(resp.text))
        if tables:
            df = tables[0]
            df = df.fillna("TBD")
    except Exception:
        pass 

    # SOURCE 2: Backup - Yahoo Finance
    if df.empty:
        try:
            url = "https://finance.yahoo.com/calendar/ipo"
            resp = yf_session.get(url, timeout=10)
            import io
            tables = pd.read_html(io.StringIO(resp.text))
            if tables:
                df = tables[0]
                df = df.fillna("TBD")
                useful_cols = [col for col in ['Symbol', 'Company', 'Exchange', 'Date', 'Price Range', 'Shares'] if col in df.columns]
                if useful_cols:
                    df = df[useful_cols]
        except Exception:
            pass

    # ADD VOLATILITY ESTIMATOR LOGIC
    if not df.empty and 'Shares' in df.columns:
        potentials = []
        for val in df['Shares']:
            val_str = str(val).replace(',', '').replace('$', '').strip().upper()
            try:
                if 'M' in val_str:
                    shares = float(val_str.replace('M', '')) * 1000000
                elif val_str == '-' or val_str == 'TBD' or val_str == 'NAN':
                    shares = -1
                else:
                    shares = float(val_str)
                    
                # The Golden Day Trading Thresholds
                if shares == -1:
                    potentials.append("❓ Unknown")
                elif shares <= 5000000:
                    potentials.append("🔥 Extreme (Low Float)")
                elif shares <= 15000000:
                    potentials.append("⭐ High")
                else:
                    potentials.append("📊 Standard (Heavy)")
            except ValueError:
                potentials.append("❓ Unknown")
                
        # Insert the rating at the very front of the table
        if 'Vol Potential' not in df.columns:
            df.insert(0, 'Vol Potential', potentials)
            
    return df

# --- 2. SCORING LOGIC ---
def analyze_day_trading_metrics(ticker_info, is_tracking=False):
    ticker, company_name = ticker_info
    time.sleep(0.5) 
    
    try:
        stock = yf.Ticker(ticker)
        df_daily = stock.history(period="1mo")
        
        if df_daily.empty: raise Exception("Yahoo returned empty data")
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

        gap_pct = float(np.nan_to_num(gap_pct))
        rvol = float(np.nan_to_num(rvol))
        adr = float(np.nan_to_num(adr))
        vwap_dist = float(np.nan_to_num(vwap_dist))
        current_rsi = float(np.nan_to_num(current_rsi))
        day_change = float(np.nan_to_num(day_change))

        score = 0
        score += int(abs(gap_pct))
        if rvol > 0: score += int(rvol / 0.5)
        if adr >= 1.0: score += int(adr)
        
        if score < 3 and not is_tracking: return None

        float_display = "N/A"
        short_display = "N/A"
        try:
            info = stock.info or {} 
            float_shares = info.get('floatShares')
            short_pct = info.get('shortPercentOfFloat')
            
            if float_shares and not pd.isna(float_shares) and float_shares > 0:
                float_display = f"{float_shares / 1000000:.1f}M"
                if float_shares < 60000000: score += int((60 - (float_shares / 1000000)) / 5) + 1
                
            if short_pct and not pd.isna(short_pct) and short_pct > 0:
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

# --- UI STYLING FUNCTIONS ---
def color_status(val):
    val_str = str(val)
    if "PEAK" in val_str: return 'color: #FF3333; font-weight: bold'  
    if "Cresting" in val_str: return 'color: #FFaa00; font-weight: bold'  
    if "Run" in val_str: return 'color: #33CC33; font-weight: bold'  
    if "Under VWAP" in val_str: return 'color: #CC0000'                     
    return ''

def color_metrics(val, column):
    try:
        if isinstance(val, str):
            if val == "N/A" or val == "TBD": return ""
            num = float(val.replace('M', '').replace('%', '').replace('x', ''))
        else:
            num = float(val)
    except ValueError:
        return ""
    
    if column == "Score":
        if num >= 10: return 'color: #33CC33; font-weight: bold'
        if num < 5: return 'color: #FF3333'
    elif column in ["Chg %", "Gap %"]:
        if num >= 5: return 'color: #33CC33; font-weight: bold'
        if num < 0: return 'color: #FF3333'
    elif column == "RVOL":
        if num >= 2.0: return 'color: #33CC33; font-weight: bold'
        if num < 1.0: return 'color: #FF3333'
    elif column == "VWAP Dist %":
        if num > 6.0 or num < 0: return 'color: #FF3333' 
        if 0 <= num <= 4.0: return 'color: #33CC33' 
    elif column == "Float":
        if num < 20: return 'color: #33CC33; font-weight: bold' 
        if num > 100: return 'color: #FF3333' 
    elif column == "Short %":
        if num > 10: return 'color: #33CC33; font-weight: bold' 
    
    return ""

def apply_styling(df):
    return df.style.map(color_status, subset=["Status"]) \
                   .map(lambda x: color_metrics(x, "Score"), subset=["Score"]) \
                   .map(lambda x: color_metrics(x, "Chg %"), subset=["Chg %"]) \
                   .map(lambda x: color_metrics(x, "Gap %"), subset=["Gap %"]) \
                   .map(lambda x: color_metrics(x, "RVOL"), subset=["RVOL"]) \
                   .map(lambda x: color_metrics(x, "VWAP Dist %"), subset=["VWAP Dist %"]) \
                   .map(lambda x: color_metrics(x, "Float"), subset=["Float"]) \
                   .map(lambda x: color_metrics(x, "Short %"), subset=["Short %"])

# --- TAB LAYOUT ---
# Added the 3rd tab here!
tab_scan, tab_watch, tab_ipo = st.tabs(["🚀 Live Scanner", "🎯 My Watchlist", "📅 IPO Notice Board"])

# ==========================================
# TAB 1: MARKET SCANNER
# ==========================================
with tab_scan:
    market_choices = [
        "UK Day Gainers (Yahoo Live)", "US Day Gainers (Yahoo Live)", "US Pre-Market Gainers (Yahoo Live)",
        "Nasdaq 100 (US)", "S&P 500 (US)", "FTSE 100 (UK)", "FTSE 250 (UK)", "Manual", "Upload Custom List (.txt)"
    ] + list(CUSTOM_LISTS.keys())
    
    market_choice = st.selectbox("🌍 Select Market to Scan:", market_choices)

    manual_tickers = ""
    uploaded_file = None
    
    if market_choice == "Manual":
        manual_tickers = st.text_input("Enter tickers (comma separated):", "TSLA, NVDA, AAPL")
    elif market_choice == "Upload Custom List (.txt)":
        uploaded_file = st.file_uploader("Upload your list (Format: TICKER, Company Name)", type=["txt"])

    if st.button("🚀 Scan Market", use_container_width=True):
        ticker_list = []
        
        if market_choice == "Manual":
            ticker_list = [(t.strip().upper(), "Manual") for t in manual_tickers.split(",") if t.strip()]
        
        elif market_choice == "Upload Custom List (.txt)":
            if uploaded_file is not None:
                content = uploaded_file.read().decode("utf-8").splitlines()
                for line in content:
                    line = line.strip()
                    if line:
                        if "," in line:
                            parts = line.split(",", 1)
                            ticker_list.append((parts[0].strip().upper(), parts[1].strip()))
                        else:
                            ticker_list.append((line.upper(), "Custom Upload"))
            else:
                st.warning("⚠️ Please upload a .txt file before scanning.")
                
        elif market_choice in CUSTOM_LISTS:
            try:
                st.info(f"Downloading {market_choice} from GitHub...")
                resp = requests.get(CUSTOM_LISTS[market_choice], timeout=10)
                resp.raise_for_status() 
                
                content = resp.text.splitlines()
                for line in content:
                    line = line.strip()
                    if line:
                        if "," in line:
                            parts = line.split(",", 1)
                            ticker_list.append((parts[0].strip().upper(), parts[1].strip()))
                        else:
                            ticker_list.append((line.upper(), "GitHub Upload"))
            except Exception as e:
                st.error(f"⚠️ Failed to fetch list from GitHub: {e}")
                
        elif "Yahoo Live" in market_choice:
            ticker_list = get_live_gainers(market_choice) 
            
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
        elif market_choice not in ["Upload Custom List (.txt)"] and market_choice not in CUSTOM_LISTS.keys():
            st.warning("⚠️ Scraper returned zero stocks. The market might be closed, or the index list is temporarily down.")

    if not st.session_state.scan_results.empty:
        st.subheader("🏆 Scanner Results")
        st.caption("Tick the box on the left to pin a stock to your Universal Watchlist.")
        
        df_display = st.session_state.scan_results.copy()
        
        if 'Track' not in df_display.columns:
            df_display.insert(0, "Track", df_display["Ticker"].isin(st.session_state.watchlist.keys()))
        else:
            df_display["Track"] = df_display["Ticker"].isin(st.session_state.watchlist.keys())
        
        edited_df = st.data_editor(
            apply_styling(df_display),
            hide_index=True,
            use_container_width=True,
            disabled=[col for col in df_display.columns if col not in ["Track"]], 
            column_config={
                "Track": st.column_config.CheckboxColumn("📌 Track"),
                "Status": st.column_config.TextColumn("Status", help="🟢 Run: Active momentum.\n⚠️ Cresting: Overbought.\n🚨 PEAK: Dump imminent.\n📉 Under VWAP: Momentum dying."),
                "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
                "Gap %": st.column_config.NumberColumn("Gap %", format="%.2f%%"),
                "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                "VWAP Dist %": st.column_config.NumberColumn("VWAP Dist %", format="%.2f%%")
            }
        )
        
        changes_made = False
        currently_tracked = edited_df[edited_df["Track"] == True]
        currently_untracked = edited_df[edited_df["Track"] == False]

        for _, row in currently_tracked.iterrows():
            if row["Ticker"] not in st.session_state.watchlist:
                st.session_state.watchlist[row["Ticker"]] = row["Company"]
                changes_made = True
                
        for _, row in currently_untracked.iterrows():
            if row["Ticker"] in st.session_state.watchlist:
                del st.session_state.watchlist[row["Ticker"]]
                changes_made = True
                
        if changes_made:
            st.rerun() 

# ==========================================
# TAB 2: MY WATCHLIST
# ==========================================
with tab_watch:
    if not st.session_state.watchlist:
        st.info("Your Watchlist is empty. Scan the market and tick the 'Track' box to pin setups here!")
    else:
        st.success(f"🎯 **Active Watchlist** ({len(st.session_state.watchlist)} stocks tracking)")
        
        if st.button("🔄 Refresh Watchlist Prices", type="primary"):
            with st.spinner("Fetching live data for tracked stocks..."):
                tracked_results = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {executor.submit(analyze_day_trading_metrics, (ticker, company), True): ticker for ticker, company in st.session_state.watchlist.items()}
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
                        apply_styling(df_tracked),
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Status": st.column_config.TextColumn("Status", help="🟢 Run: Active momentum.\n⚠️ Cresting: Overbought.\n🚨 PEAK: Dump imminent.\n📉 Under VWAP: Momentum dying."),
                            "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                            "Chg %": st.column_config.NumberColumn("Chg %", format="%.2f%%"),
                            "Gap %": st.column_config.NumberColumn("Gap %", format="%.2f%%"),
                            "RVOL": st.column_config.NumberColumn("RVOL", format="%.2fx"),
                            "VWAP Dist %": st.column_config.NumberColumn("VWAP Dist %", format="%.2f%%")
                        }
                    )

# ==========================================
# TAB 3: IPO NOTICE BOARD
# ==========================================
with tab_ipo:
    st.subheader("📅 Upcoming IPOs (Initial Public Offerings)")
    st.markdown("Track brand new companies about to go live on the markets. Keep an eye on these for massive Day 1 volatility!")
    
    with st.spinner("Fetching this week's IPO calendar..."):
        df_ipo = get_ipo_calendar()
        
        if not df_ipo.empty:
            st.dataframe(
                df_ipo,
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No upcoming IPOs found for this timeframe, or the calendar is temporarily unavailable.")
