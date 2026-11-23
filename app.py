import streamlit as st
import pandas as pd
from datetime import datetime
from nse_option_chain import fetch_option_chain, save_csv

st.set_page_config(page_title="NSE Option Chain", page_icon="📊", layout="wide")
st.title("📊 NSE India — Option Chain Dashboard")
st.caption("Data is fetched from the NSE option-chain API using your requests-based approach.")

with st.sidebar:
    st.header("Settings")
    symbol = st.selectbox("Index", ["NIFTY", "BANKNIFTY", "FINNIFTY"])
    expiry = st.text_input("Expiry (e.g. 23-Nov-2026)", "23-Nov-2026")
    # expiry_dates = st.multiselect(
    #     "Select Expiry Dates",
    #     [
    #         "23-Nov-2026",
    #         "30-Nov-2026",
    #         "07-Dec-2026",
    #         "14-Dec-2026",
    #         "21-Dec-2026",
    #         "28-Dec-2026"
    #     ],
    #     default=["23-Nov-2026"]
    # )
    refresh = st.button("🔄 Fetch / Refresh", type="primary")
    st.caption("NSE may rate-limit or block automated requests. Refresh manually if needed.")



if refresh or "option_df" not in st.session_state:
    with st.spinner("Connecting to NSE and fetching option chain..."):
        try:
            df, raw = fetch_option_chain(symbol=symbol, expiry=expiry)
            st.session_state.option_df = df
            st.session_state.last_symbol = symbol
            st.session_state.last_expiry = expiry
            st.session_state.fetched_at = datetime.now()
            st.session_state.fetch_error = None
        except Exception as exc:
            st.session_state.fetch_error = str(exc)

if st.session_state.get("fetch_error"):
    st.error(st.session_state.fetch_error)

if "option_df" in st.session_state:
    df = st.session_state.option_df
    spot = None
    # NSE payload can contain underlying value in records; keep this optional.
    st.caption(
        f"Showing {st.session_state.last_symbol} · Expiry {st.session_state.last_expiry} · "
        f"Fetched {st.session_state.fetched_at:%d-%b-%Y %H:%M:%S}"
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Option-chain rows", f"{len(df):,}")
    c2.metric("Strike range", f"{df['STRIKE'].min():,.0f} – {df['STRIKE'].max():,.0f}")
    ce_oi = pd.to_numeric(df["CE OI"], errors="coerce").fillna(0).sum()
    pe_oi = pd.to_numeric(df["PE OI"], errors="coerce").fillna(0).sum()
    c3.metric("PCR (OI)", f"{pe_oi / ce_oi:.2f}" if ce_oi else "N/A")

    st.subheader("Option Chain")
    st.dataframe(
        df.style.format(precision=2, na_rep="-"),
        use_container_width=True,
        height=600
    )

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv,
        file_name=f"{st.session_state.last_symbol}_option_chain.csv",
        mime="text/csv"
    )

    st.subheader("Open Interest")
    chart_df = df.set_index("STRIKE")[["CE OI", "PE OI"]].apply(
        pd.to_numeric, errors="coerce"
    ).fillna(0)
    st.bar_chart(chart_df)

st.divider()
st.caption(
    "Educational / analytical use only. This is not an order-execution or trading system. "
    "NSE website endpoints are unofficial for third-party automation and can change without notice."
)
