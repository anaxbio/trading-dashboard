# --- ADD THIS TO YOUR DATA ENGINES SECTION ---
def get_swing_stops(sym):
    try:
        t = yf.Ticker(f"{sym}.NS")
        hist = t.history(period="50d")
        if hist.empty: return 0.0, 0.0
        
        # 1. Hard Stop: Low of the entry day (or previous day)
        hard_sl = hist['Low'].iloc[-1]
        
        # 2. Trailing Stop: 20-Day SMA (Institutional Support)
        trail_sl = hist['Close'].rolling(20).mean().iloc[-1]
        
        return round(hard_sl, 2), round(trail_sl, 2)
    except: return 0.0, 0.0

# --- REPLACE TAB 2 STEP 2 WITH THIS ---
st.divider()
st.subheader("Step 2: Swing Portfolio & Risk Guard")
try:
    df_sw = conn.read(worksheet="SWING_PORTFOLIO", ttl=0).dropna(how='all')
    active_sw = df_sw[df_sw['Status'].astype(str).str.upper() == 'OPEN'].copy()
    
    if not active_sw.empty:
        sw_rows = []
        for _, r in active_sw.iterrows():
            hard, trail = get_swing_stops(r['Symbol'])
            # Get current LTP for P&L
            t_live = yf.Ticker(f"{r['Symbol']}.NS")
            curr_ltp = t_live.fast_info['last_price']
            pnl = ((curr_ltp - float(r['Entry_Price'])) / float(r['Entry_Price'])) * 100
            
            sw_rows.append({
                "Symbol": r['Symbol'],
                "Entry": r['Entry_Price'],
                "LTP": round(curr_ltp, 2),
                "P&L%": f"{round(pnl, 2)}%",
                "HARD SL (Day Low)": hard,
                "TRAIL SL (20-SMA)": trail,
                "Status": "✅ HEALTHY" if curr_ltp > trail else "⚠️ CLOSE TO TRAIL"
            })
        st.table(pd.DataFrame(sw_rows))
        st.caption("💡 Tip: Use HARD SL for the first 3 days. Switch to TRAIL SL once you are 5%+ in profit.")
    else:
        st.info("Swing portfolio is currently empty.")
except Exception as e:
    st.info("Add stocks from the scanner above to see your risk guard.")
