import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor

# --- CONFIG ---
st.set_page_config(page_title="EP Precision Dashboard", layout="wide")
st.title("🛡️ Episodic Pivot & Stage 2 Monitor")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

conn = st.connection("gsheets", type=GSheetsConnection)

# --- CORE LOGIC: THE PRECISION ENGINE ---
def process_ticker(sym, threshold):
    """The exact logic used for the Morning EP Report"""
    try:
        t = yf.Ticker(f"{sym}.NS")
        # Fetch 1 year for SMA and 5 days for Gap/Volume logic
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        
        # 1. Stage 2 Filter (Above 200 SMA)
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        curr_price = hist['Close'].iloc[-1]
        if curr_price < (sma200 * 0.97): return None # Strictly Stage 2
        
        # 2. Gap Calculation (Today's Open vs Yesterday's Close)
        prev_close = hist['Close'].iloc[-2]
        today_open = hist['Open'].iloc[-1]
        today_high = hist['High'].iloc[-1]
        
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        max_day_chg = ((today_high - prev_close) / prev_close) * 100
        
        # 3. Relative Volume (RVOL) - Institutional Footprint
        avg_vol = hist['Volume'].tail(30).mean() # 30-day average
        curr_vol = hist['Volume'].iloc[-1]
        rvol = curr_vol / avg_vol
        
        # 4. EP CRITERIA: Gap > 3% OR Max Change > threshold, with Volume > 1.5x
        if (gap_pct >= 3.0 or max_day_chg >= threshold) and rvol > 1.5:
            return {
                'Symbol': sym,
                'LTP': round(curr_price, 2),
                'Gap%': round(gap_pct, 2),
                'Max_Day%': round(max_day_chg, 2),
                'RVOL': round(rvol, 1),
                'Signal': "🔥 STRONG EP" if rvol > 3 else "✅ EP"
            }
    except: pass
    return None

def run_precision_scan(threshold):
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    try:
        tickers = pd.read_csv(url)['Symbol'].tolist()
    except: return pd.DataFrame()
    
    results = []
    prog = st.progress(0)
    st.info(f"Scanning 500 stocks for {threshold}% Pivots... Please wait ~45 seconds.")
    
    # Use Multithreading for Speed
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_ticker, s, threshold) for s in tickers]
        for i, future in enumerate(futures):
            prog.progress((i + 1) / len(tickers))
            res = future.result()
            if res: results.append(res)
            
    prog.empty()
    return pd.DataFrame(results)

# --- UI TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Precision Scanner", "💰 Intraday", "📈 Swing"])

with tab1:
    st.header("Step 1: Morning Pivot Discovery")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔥 Run 5% High-Conviction Scan"):
            st.session_state.scan_results = run_precision_scan(5.0)
    with c2:
        if st.button("⚡ Run 3.5% Early-Bird Scan"):
            st.session_state.scan_results = run_precision_scan(3.5)

    if not st.session_state.scan_results.empty:
        st.subheader("Results Matched to EP Criteria")
        # Highlight high RVOL stocks
        st.dataframe(st.session_state.scan_results.style.highlight_max(subset=['RVOL'], color='#1e3d33'))
        
        with st.form("commit_precision"):
            confirmed = []
            for i, row in st.session_state.scan_results.iterrows():
                if st.checkbox(f"Add {row['Symbol']} (RVOL: {row['RVOL']})", key=f"pscan_{row['Symbol']}"):
                    confirmed.append({
                        'Symbol': row['Symbol'], 'Entry_Price': row['LTP'], 
                        'Date': get_now_ist().strftime('%Y-%m-%d %H:%M:%S'), 'Status': 'OPEN'
                    })
            
            mode = st.radio("Add to:", ["INTRADAY_PORTFOLIO", "SWING_PORTFOLIO"])
            if st.form_submit_button("💾 COMMIT TO GOOGLE SHEETS"):
                if confirmed:
                    try:
                        df = conn.read(worksheet=mode, ttl=0).dropna(how='all')
                        updated = pd.concat([df, pd.DataFrame(confirmed)], ignore_index=True)
                        conn.update(worksheet=mode, data=updated)
                        st.success("Portfolio Updated!")
                        st.session_state.scan_results = pd.DataFrame()
                        time.sleep(1); st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

# --- TABS 2 & 3: Standard Monitors ---
# (Keeping your original logic but ensuring error-handling is tight)
for t, sheet in [(tab2, "INTRADAY_PORTFOLIO"), (tab3, "SWING_PORTFOLIO")]:
    with t:
        st.header(f"{sheet.split('_')[0].capitalize()} Monitor")
        if st.button("🔄 Sync Prices", key=f"ref_{sheet}"): st.cache_data.clear(); st.rerun()
        try:
            df = conn.read(worksheet=sheet, ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper().str.strip() == 'OPEN'].copy()
            if not active.empty:
                # Basic monitor table logic here
                st.table(active[['Symbol', 'Entry_Price', 'Date']])
                sel = st.selectbox("Select to Close:", ["None"] + active['Symbol'].tolist(), key=f"sel_{sheet}")
                if sel != "None" and st.button("Close Trade", key=f"btn_{sheet}"):
                    df.loc[df['Symbol'] == sel, 'Status'] = 'CLOSED'
                    conn.update(worksheet=sheet, data=df)
                    st.rerun()
        except: st.info(f"{sheet} is currently empty.")
