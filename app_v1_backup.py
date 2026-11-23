import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from nse_option_chain import fetch_option_chain

# ------------------------------ App setup ------------------------------
st.set_page_config(
    page_title="NSE Options | Paper Trading",
    page_icon="📈",
    layout="wide",
)

DB_PATH = Path(__file__).with_name("paper_trading.db")
INDEXES = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
OPTION_TYPES = ["CE", "PE"]


# ------------------------------ Persistence ----------------------------
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            expiry TEXT NOT NULL,
            option_type TEXT NOT NULL,
            strike REAL NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'EXECUTED'
        )
        """
    )
    conn.commit()
    return conn


def save_order(order):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO orders
            (order_time, symbol, expiry, option_type, strike, side, quantity, price, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTED')
            """,
            (
                order["order_time"],
                order["symbol"],
                order["expiry"],
                order["option_type"],
                order["strike"],
                order["side"],
                order["quantity"],
                order["price"],
            ),
        )


def load_orders():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT * FROM orders ORDER BY id DESC", conn
        )


def clear_orders():
    with get_connection() as conn:
        conn.execute("DELETE FROM orders")


# ------------------------------ P&L engine ------------------------------
def calculate_positions(orders, current_chain, symbol, expiry):
    """Build net positions and realized/unrealized P&L from the paper order ledger."""
    if orders.empty:
        return pd.DataFrame(), 0.0

    relevant = orders[
        (orders["symbol"] == symbol) & (orders["expiry"] == expiry)
    ].copy()
    if relevant.empty:
        return pd.DataFrame(), 0.0

    # Process chronologically so realized P&L is calculated consistently.
    relevant = relevant.sort_values("id")
    states = {}

    for _, order in relevant.iterrows():
        key = (order["option_type"], float(order["strike"]))
        state = states.setdefault(
            key, {"net_qty": 0, "avg_price": 0.0, "realized": 0.0}
        )

        qty = int(order["quantity"])
        trade_sign = 1 if order["side"] == "BUY" else -1
        trade_qty = trade_sign * qty
        trade_price = float(order["price"])
        old_qty = state["net_qty"]

        if old_qty == 0 or old_qty * trade_qty > 0:
            new_qty = old_qty + trade_qty
            total_cost = abs(old_qty) * state["avg_price"] + abs(trade_qty) * trade_price
            state["avg_price"] = total_cost / abs(new_qty) if new_qty else 0.0
            state["net_qty"] = new_qty
        else:
            closed_qty = min(abs(old_qty), abs(trade_qty))
            if old_qty > 0:  # Closing a long position with a sell
                state["realized"] += (trade_price - state["avg_price"]) * closed_qty
            else:  # Closing a short position with a buy
                state["realized"] += (state["avg_price"] - trade_price) * closed_qty

            new_qty = old_qty + trade_qty
            if new_qty == 0:
                state["avg_price"] = 0.0
            elif old_qty * new_qty < 0:  # Trade crossed through zero
                state["avg_price"] = trade_price
            state["net_qty"] = new_qty

    ltp_lookup = {}
    if current_chain is not None and not current_chain.empty:
        for _, row in current_chain.iterrows():
            strike = pd.to_numeric(row.get("STRIKE"), errors="coerce")
            if pd.isna(strike):
                continue
            for option_type in OPTION_TYPES:
                ltp = pd.to_numeric(row.get(f"{option_type} LTP"), errors="coerce")
                if pd.notna(ltp):
                    ltp_lookup[(option_type, float(strike))] = float(ltp)

    rows = []
    total_realized = 0.0
    for (option_type, strike), state in states.items():
        net_qty = state["net_qty"]
        avg_price = state["avg_price"]
        ltp = ltp_lookup.get((option_type, strike))
        unrealized = (
            (ltp - avg_price) * net_qty if ltp is not None else None
        )
        total_realized += state["realized"]
        rows.append(
            {
                "Option": option_type,
                "Strike": strike,
                "Net Qty": net_qty,
                "Avg. Price": avg_price,
                "LTP": ltp,
                "Realized P&L": state["realized"],
                "Unrealized P&L": unrealized,
                "Total P&L": (
                    state["realized"] + unrealized
                    if unrealized is not None
                    else None
                ),
            }
        )

    positions = pd.DataFrame(rows)
    if not positions.empty:
        positions = positions.sort_values(["Strike", "Option"])
    return positions, total_realized


# ------------------------------ Formatting helpers ---------------------
def money(value):
    if value is None or pd.isna(value):
        return "—"
    return f"₹{value:,.2f}"


def show_metric_row(chain, orders, symbol, expiry):
    positions, realized = calculate_positions(orders, chain, symbol, expiry)
    unrealized = (
        pd.to_numeric(positions["Unrealized P&L"], errors="coerce").fillna(0).sum()
        if not positions.empty
        else 0.0
    )
    total = realized + unrealized
    total_orders = len(
        orders[(orders["symbol"] == symbol) & (orders["expiry"] == expiry)]
    ) if not orders.empty else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Realized P&L", money(realized))
    m2.metric("Unrealized P&L", money(unrealized))
    m3.metric("Net P&L", money(total), delta=money(total))
    m4.metric("Executed orders", f"{total_orders:,}")
    return positions


# ------------------------------ Sidebar ---------------------------------
with st.sidebar:
    st.title("📈 NSE Options")
    st.caption("Live option-chain data + simulated paper orders")
    symbol = st.selectbox("Index", INDEXES, index=0)
    expiry = st.text_input("Expiry date", value="23-Nov-2026")
    refresh = st.button("🔄 Fetch / Refresh market data", type="primary", use_container_width=True)
    st.caption(
        "Expiry format: DD-MMM-YYYY. NSE may rate-limit automated requests; "
        "refresh manually if a request fails."
    )

# ------------------------------ Fetch data ------------------------------
if refresh or "option_df" not in st.session_state:
    with st.spinner("Fetching option chain from NSE..."):
        try:
            chain, raw_payload = fetch_option_chain(symbol=symbol, expiry=expiry)
            st.session_state.option_df = chain
            st.session_state.last_symbol = symbol
            st.session_state.last_expiry = expiry
            st.session_state.fetched_at = datetime.now()
            st.session_state.fetch_error = None
        except Exception as exc:
            st.session_state.fetch_error = str(exc)

if st.session_state.get("fetch_error"):
    st.error(f"Market-data error: {st.session_state.fetch_error}")

chain_available = "option_df" in st.session_state
chain = st.session_state.option_df if chain_available else pd.DataFrame()

# Use the latest successfully fetched chain only when it matches current controls.
same_selection = (
    chain_available
    and st.session_state.get("last_symbol") == symbol
    and st.session_state.get("last_expiry") == expiry
)
if not same_selection:
    chain = pd.DataFrame()

orders = load_orders()

st.title("NSE Index Options — Trading Dashboard")
st.caption("Paper trading only. Orders are simulated at the selected option's displayed LTP.")

if same_selection:
    st.caption(
        f"Market data: {symbol} · Expiry: {expiry} · "
        f"Last fetched: {st.session_state.fetched_at:%d-%b-%Y %H:%M:%S}"
    )
else:
    st.info("Choose an index and expiry, then click **Fetch / Refresh market data** to load the option chain.")

# ------------------------------ Main pages ------------------------------
dashboard_tab, trading_tab = st.tabs(["📊 Dashboard", "🛒 Option Chain & Buy / Sell"])

with dashboard_tab:
    st.subheader("P&L Overview")
    positions = show_metric_row(chain, orders, symbol, expiry)

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("#### Open positions")
        if positions.empty:
            st.info("No paper positions for this index and expiry yet.")
        else:
            open_positions = positions[positions["Net Qty"] != 0].copy()
            if open_positions.empty:
                st.info("No open positions. Your closed trades remain in the order history.")
            else:
                st.dataframe(
                    open_positions,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Avg. Price": st.column_config.NumberColumn(format="₹%.2f"),
                        "LTP": st.column_config.NumberColumn(format="₹%.2f"),
                        "Realized P&L": st.column_config.NumberColumn(format="₹%.2f"),
                        "Unrealized P&L": st.column_config.NumberColumn(format="₹%.2f"),
                        "Total P&L": st.column_config.NumberColumn(format="₹%.2f"),
                    },
                )
    with right:
        st.markdown("#### Order activity")
        filtered_orders = (
            orders[(orders["symbol"] == symbol) & (orders["expiry"] == expiry)]
            if not orders.empty
            else orders
        )
        if filtered_orders.empty:
            st.info("No orders placed yet.")
        else:
            activity = filtered_orders.head(8)[
                ["order_time", "option_type", "strike", "side", "quantity", "price"]
            ].copy()
            activity.columns = ["Time", "Type", "Strike", "Side", "Qty", "Price"]
            st.dataframe(activity, use_container_width=True, hide_index=True)

    st.markdown("#### P&L by position")
    if not positions.empty:
        chart_data = positions.set_index(
            positions["Option"] + " " + positions["Strike"].map(lambda x: f"{x:g}")
        )[["Realized P&L", "Unrealized P&L"]].fillna(0)
        st.bar_chart(chart_data)

    st.divider()
    st.markdown("#### Complete order book")
    if orders.empty:
        st.info("Your executed paper orders will be stored here.")
    else:
        display_orders = orders.copy()
        display_orders["order_time"] = pd.to_datetime(display_orders["order_time"]).dt.strftime(
            "%d-%b-%Y %H:%M:%S"
        )
        st.dataframe(
            display_orders.rename(
                columns={
                    "order_time": "Time",
                    "symbol": "Index",
                    "expiry": "Expiry",
                    "option_type": "Type",
                    "strike": "Strike",
                    "side": "Side",
                    "quantity": "Qty",
                    "price": "Price",
                    "status": "Status",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        csv_orders = orders.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download order history CSV",
            csv_orders,
            file_name="paper_order_history.csv",
            mime="text/csv",
        )

with trading_tab:
    st.subheader("Option Chain")
    if chain.empty:
        st.warning("Option chain is not loaded for the selected index and expiry.")
    else:
        # Add a combined strike-centered view, keeping CE and PE values side by side.
        display_columns = [
            "CE OI", "CE CHNG IN OI", "CE VOLUME", "CE IV", "CE LTP",
            "CE CHNG", "CE BID", "CE ASK", "STRIKE",
            "PE BID", "PE ASK", "PE CHNG", "PE LTP", "PE IV",
            "PE VOLUME", "PE CHNG IN OI", "PE OI",
        ]
        available_columns = [c for c in display_columns if c in chain.columns]
        st.dataframe(
            chain[available_columns].style.format(precision=2, na_rep="—"),
            use_container_width=True,
            height=520,
        )
        st.download_button(
            "⬇️ Download option chain CSV",
            chain.to_csv(index=False).encode("utf-8"),
            file_name=f"{symbol}_{expiry.replace('-', '_')}_option_chain.csv",
            mime="text/csv",
        )

    st.divider()
    st.subheader("Place Paper Order")
    if chain.empty:
        st.info("Load market data first. The order form uses the selected option's current LTP.")
    else:
        strike_values = sorted(
            pd.to_numeric(chain["STRIKE"], errors="coerce").dropna().unique().tolist()
        )
        with st.form("paper_order_form", clear_on_submit=False):
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                order_side = st.selectbox("Action", ["BUY", "SELL"])
            with c2:
                option_type = st.selectbox("Option type", OPTION_TYPES)
            with c3:
                strike = st.selectbox("Strike price", strike_values)
            with c4:
                quantity = st.number_input("Quantity (units)", min_value=1, value=1, step=1)

            selected_row = chain[
                pd.to_numeric(chain["STRIKE"], errors="coerce") == float(strike)
            ]
            ltp_column = f"{option_type} LTP"
            selected_ltp = (
                pd.to_numeric(selected_row.iloc[0][ltp_column], errors="coerce")
                if not selected_row.empty
                else pd.NA
            )
            if pd.notna(selected_ltp):
                st.info(
                    f"Selected contract: **{symbol} {expiry} {strike:g} {option_type}** "
                    f"· Current LTP: **₹{float(selected_ltp):,.2f}** "
                    f"· Estimated order value: **₹{float(selected_ltp) * int(quantity):,.2f}**"
                )
            else:
                st.warning("LTP is unavailable for this contract. Choose another strike/type.")

            confirm_order = st.form_submit_button(
                "Place paper order", type="primary", use_container_width=True
            )

        if confirm_order:
            if pd.isna(selected_ltp) or float(selected_ltp) <= 0:
                st.error("Cannot place order because a valid LTP is not available.")
            else:
                save_order(
                    {
                        "order_time": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "expiry": expiry,
                        "option_type": option_type,
                        "strike": float(strike),
                        "side": order_side,
                        "quantity": int(quantity),
                        "price": float(selected_ltp),
                    }
                )
                st.success(
                    f"Paper {order_side} executed: {quantity} unit(s) of "
                    f"{symbol} {strike:g} {option_type} at ₹{float(selected_ltp):,.2f}."
                )
                st.rerun()

st.divider()
st.caption(
    "Important: This app does not send orders to a broker. Paper orders are stored locally "
    "in paper_trading.db. P&L is estimated from simulated fills and the latest fetched LTP. "
    "NSE website endpoints are unofficial, may change, and can be rate-limited. "
    "Use a broker API and proper risk controls before considering live execution."
)
