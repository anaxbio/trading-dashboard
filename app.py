import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_gsheets import GSheetsConnection
from datetime import datetime
import time
import pytz
from concurrent.futures import ThreadPoolExecutor

# --- CONFIG ---
st.set_page_config(page_title="EP 5X Precision Dashboard", layout="wide")
st.title("🛡️ Episodic Pivot & 5X Leverage Monitor")

def get_now_ist():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = pd.DataFrame()

# Connection to Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- HELPER: DYNAMIC VWAP CALCULATION ---
def get_vwap_data(sym):
    """Fetches intraday data and calculates the Cumulative VWAP for the day."""
    try:
        t = yf.Ticker(f"{sym}.NS")
        # 1-minute interval is critical for precise intraday VWAP
        df = t.history(period="1d", interval="1m")
        if df.empty or df['Volume'].sum() == 0: 
            return 0.0, 0.0
        
        # Standard VWAP Formula: Sum(Typical Price * Volume) / Sum(Volume)
        df['TP'] = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (df['TP'] * df['Volume']).sum() / df['Volume'].sum()
        return round(df['Close'].iloc[-1], 2), round(vwap, 2)
    except:
        return 0.0, 0.0

# --- CORE LOGIC: THE PRECISION SCANNER (Strict 200 SMA) ---
def process_ticker(sym, threshold):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="1y")
        if len(hist) < 200: return None
        
        # THE WALL: Strictly Price > 200 SMA (Non-Negotiable)
        sma200 = hist['Close'].rolling(200).mean().iloc[-1]
        curr_price = hist['Close'].iloc[-1]
        if curr_price < sma200: return None 
        
        # EP Calculations
        prev_close = hist['Close'].iloc[-2]
        today_open = hist['Open'].iloc[-1]
        today_high = hist['High'].iloc[-1]
        
        gap_pct = ((today_open - prev_close) / prev_close) * 100
        max_day_chg = ((today_high - prev_close) / prev_close) * 100
        
        avg_vol = hist['Volume'].tail(30).mean()
        curr_vol = hist['Volume'].iloc[-1]
        rvol = curr_vol / avg_vol
        
        # 4. Filter Logic
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
    st.info(f"Scanning Nifty 500 for {threshold}% Pivots... (SMA Wall Active)")
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_ticker, s, threshold) for s in tickers]
        for i, future in enumerate(futures):
            prog.progress((i + 1) / len(tickers))
            res = future.result()
            if res: results.append(res)
            
    prog.empty()
    return pd.DataFrame(results)

# --- UI TABS ---
tab1, tab2, tab3 = st.tabs(["🚀 Precision Scanner", "💰 5X Intraday Cockpit", "📈 Swing Monitor"])

# --- TAB 1: SCANNER ---
with tab1:
    st.header("Step 1: Morning Pivot Discovery")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔥 Run 5% Primary Scan"):
            st.session_state.scan_results = run_precision_scan(5.0)
    with c2:
        if st.button("⚡ Run 3.5% Early Scan"):
            st.session_state.scan_results = run_precision_scan(3.5)

    if not st.session_state.scan_results.empty:
        st.subheader("Stage 2 Results")
        st.dataframe(st.session_state.scan_results)
        
        with st.form("commit_precision"):
            confirmed = []
            for i, row in st.session_state.scan_results.iterrows():
                if st.checkbox(f"Add {row['Symbol']}", key=f"pscan_{row['Symbol']}"):
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
                        st.success("Sheets Updated Successfully!")
                        st.session_state.scan_results = pd.DataFrame()
                        time.sleep(1); st.rerun()
                    except Exception as e: st.error(f"Sheet Error: {e}")

# --- TAB 2: 5X INTRADAY COCKPIT ---
with tab2:
    st.header("💰 5X Leverage Management")
    
    @st.fragment(run_every="120s")
    def live_cockpit():
        st.caption(f"Next Auto-Sync: {get_now_ist().strftime('%H:%M:%S')} (Interval: 2m)")
        try:
            df = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active = df[df['Status'].astype(str).str.upper() == 'OPEN'].copy()
            
            if not active.empty:
                rows = []
                for _, row in active.iterrows():
                    ltp, vwap = get_vwap_data(row['Symbol'])
                    entry = float(row['Entry_Price'])
                    
                    # SYSTEM STOP LOSS (VWAP with 0.1% safety buffer)
                    sys_sl = round(vwap * 0.999, 2)
                    
                    # 5X Leverage Math
                    stock_pnl = ((ltp - entry) / entry) * 100
                    capital_pnl = stock_pnl * 5
                    
                    rows.append({
                        "Symbol": row['Symbol'],
                        "LTP": ltp,
                        "Entry": entry,
                        "SYSTEM SL (VWAP)": sys_sl,
                        "5X CAP P&L": f"{round(capital_pnl, 2)}%",
                        "Signal": "✅ HOLD" if ltp > sys_sl else "🚨 EXIT"
                    })
                st.table(pd.DataFrame(rows))
                st.warning("⚠️ If 'Signal' flips to EXIT, trigger your stop in Stoxkart immediately.")
            else:
                st.info("No active 5X positions found.")
        except: st.error("Syncing with Google Sheets...")

    live_cockpit()

    # Manual Close Form
    with st.expander("Record Trade Exit"):
        try:
            df_close = conn.read(worksheet="INTRADAY_PORTFOLIO", ttl=0).dropna(how='all')
            active_list = df_close[df_close['Status'].astype(str).str.upper() == 'OPEN']['Symbol'].tolist()
            sel = st.selectbox("Select stock to close:", ["None"] + active_list)
            if sel != "None" and st.button("Confirm Manual Close"):
                df_close.loc[df_close['Symbol'] == sel, 'Status'] = 'CLOSED'
                conn.update(worksheet="INTRADAY_PORTFOLIO", data=df_close)
                st.success(f"Closed {sel} in Sheets.")
                time.sleep(1); st.rerun()
        except: pass

# --- TAB 3: SWING MONITOR ---
with tab3:
    st.header("📈 Swing Positions")
    try:
        df_s = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
        active_s = df_s[df_s['Status'].astype(str).str.upper() == 'OPEN']
        if not active_s.empty:
            st.table(active_s[['Symbol', 'Entry_Price', 'Date']])
        else:
            st.info("No active swing positions.")
    except: pass
