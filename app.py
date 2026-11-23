import re
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import demo_data
import paper
from nse_option_chain import extract_spot, fetch_expiries, fetch_option_chain

st.set_page_config(page_title="NSE Options · Paper Trading", page_icon="📈", layout="wide")

INDEXES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60,"MIDCPNIFTY": 120, "HDFCBANK":650}  # default lot sizes - editable in sidebar
GREEN, RED, AMBER = "#0e9f6e", "#e5484d", "#f5a524"
ss = st.session_state

st.markdown(f"""<style>
.block-container {{padding-top: 1.4rem; max-width: 1500px;}}
.stButton > button {{width: 100%;}}
.pos {{color: {GREEN};}} .neg {{color: {RED};}} .muted {{opacity: .62; font-size: .82rem;}}
.num {{font-variant-numeric: tabular-nums;}}
.hero .big {{font-size: 2.7rem; font-weight: 700; line-height: 1.05; font-variant-numeric: tabular-nums;}}
.hero .lbl {{font-size: .9rem; opacity: .65;}}
.badge {{display:inline-block; padding: 1px 9px; border-radius: 99px; font-size: .75rem; font-weight: 600;}}
.badge.live {{background: rgba(14,159,110,.16); color: {GREEN};}}
.badge.demo {{background: rgba(245,165,36,.18); color: {AMBER};}}
.oi {{padding: .32rem .5rem; border-radius: 4px; font-size: .84rem; font-variant-numeric: tabular-nums; white-space: nowrap;}}
.strike {{text-align: center; font-weight: 700; padding: .32rem 0; border-radius: 4px; font-variant-numeric: tabular-nums;}}
.atm {{background: rgba(245,165,36,.25);}}
.spotline {{border-top: 1px dashed {AMBER}; text-align: center; font-size: .72rem; color: {AMBER}; margin: 3px 0 1px;}}
.st-key-ladder [data-testid="stVerticalBlock"] {{gap: .18rem;}}
.st-key-ladder .stButton > button {{min-height: 2.05rem; padding: .1rem .3rem; font-variant-numeric: tabular-nums;}}
</style>""", unsafe_allow_html=True)


# ------------------------------ helpers ------------------------------
def inr(x, d=2, signed=False):
    """Indian-grouped rupee string (12,34,567.89)."""
    if x is None or pd.isna(x):
        return "—"
    ip, _, fp = f"{abs(x):.{d}f}".partition(".")
    if len(ip) > 3:
        ip = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", ip[:-3]) + "," + ip[-3:]
    sign = "-" if x < 0 and round(abs(x), d) > 0 else ("+" if signed and x > 0 else "")
    return f"{sign}₹{ip}" + (f".{fp}" if d else "")


def cls(x):
    return "pos" if (x or 0) >= 0 else "neg"


def short(n):
    n = 0 if n is None or pd.isna(n) else float(n)
    return f"{n/1e5:.2f}L" if abs(n) >= 1e5 else (f"{n/1e3:.1f}K" if abs(n) >= 1e3 else f"{n:.0f}")


def lot_of(symbol):
    return int(ss.get(f"lot_{symbol}", INDEXES[symbol]))


def fig_style(fig, h=320):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12, x=0),
                      hovermode="x unified")
    return fig


# ------------------------------ state ------------------------------
for k, v in dict(chain=pd.DataFrame(), spot=None, marks={}, fetched_at=None, error=None, sel=None,
                 data_ts=0.0, dialog_ts=0.0, expiries={}, demo_state={}).items():
    ss.setdefault(k, v)


def dialog_open():
    return time.time() - ss.dialog_ts < 90


def get_expiries(symbol, demo, force=False):
    key = (demo, symbol)
    cached = ss.expiries.get(key)
    if cached and not force and (cached["list"] or time.time() - cached["t"] < 60):
        return cached["list"]
    if demo:
        lst = demo_data.demo_expiries()
    else:
        try:
            lst = fetch_expiries(symbol)
        except Exception:
            lst = []
    ss.expiries[key] = {"list": lst, "t": time.time()}
    return lst


def load_market(demo, symbol, expiry):
    ss.data_ts, new_sel = time.time(), (demo, symbol, expiry)
    if ss.sel != new_sel:
        ss.chain, ss.spot = pd.DataFrame(), None
    ss.sel = new_sel
    try:
        if demo:
            spot = demo_data.tick(ss.demo_state, symbol)
            chain = demo_data.build_chain(ss.demo_state, symbol, expiry)
        else:
            chain, payload = fetch_option_chain(symbol, expiry)
            spot = extract_spot(payload, chain)
        ss.chain, ss.spot, ss.fetched_at, ss.error = chain, spot, datetime.now(), None
        for r in chain[["STRIKE", "CE LTP", "PE LTP"]].itertuples(index=False):
            for typ, v in (("CE", r[1]), ("PE", r[2])):
                if pd.notna(v):
                    ss.marks[(symbol, expiry, typ, float(r[0]))] = float(v)
    except Exception as exc:
        ss.error = str(exc)


def chain_stats(chain, spot):
    d = chain.apply(pd.to_numeric, errors="coerce")
    ce, pe = d["CE OI"].fillna(0), d["PE OI"].fillna(0)
    k = d["STRIKE"].values
    pain = pd.Series([(ce.values * np.maximum(p - k, 0)).sum() + (pe.values * np.maximum(k - p, 0)).sum() for p in k], index=k)
    atm = k[np.abs(k - spot).argmin()]
    row = d[d["STRIKE"] == atm].iloc[0]
    return dict(pcr=pe.sum() / ce.sum() if ce.sum() else None, res=d.loc[ce.idxmax(), "STRIKE"],
                sup=d.loc[pe.idxmax(), "STRIKE"], maxpain=pain.idxmin(), pain=pain, atm=atm,
                straddle=row["CE LTP"] + row["PE LTP"], d=d)


# ------------------------------ sidebar ------------------------------
with st.sidebar:
    st.markdown("### 📈 NSE Options")
    source = st.radio("Data source", ["Live NSE", "Demo (simulated)"], help="Demo works anytime - great for practice when the market is closed or NSE blocks requests.")
    demo = source.startswith("Demo")
    symbol = st.selectbox("Index", list(INDEXES))
    expiries = get_expiries(symbol, demo)
    if expiries:
        expiry = st.selectbox("Expiry", expiries)
    else:
        expiry = st.text_input("Expiry (DD-Mon-YYYY)", "24-Nov-2026", help="Couldn't load the expiry list from NSE - type one manually.")
    refresh = st.button("🔄 Refresh now", type="primary")
    if refresh:
        get_expiries(symbol, demo, force=True)
    st.divider()
    window = st.slider("Strikes shown around ATM", 5, 30, 10)
    auto = st.toggle("Auto-refresh", value=demo, help="Pauses while a trade pop-up is open.")
    interval = st.select_slider("Every (seconds)", [5, 10, 15, 30, 60, 120], value=10 if demo else 30,
                                help="Live NSE: keep this at 15s or more to avoid being blocked.")
    st.number_input("Lot size", 1, 100000, INDEXES[symbol], key=f"lot_{symbol}",
                    help="Check the latest NSE circular - lot sizes are revised from time to time.")
    capital = st.number_input("Starting capital (₹)", 10000, 100000000, paper.get_setting("capital", 1000000), step=50000)
    if capital != paper.get_setting("capital", 1000000):
        paper.set_setting("capital", capital)

# ------------------------------ data + P&L ------------------------------
if expiry and (ss.sel != (demo, symbol, expiry) or refresh or (auto and not dialog_open() and time.time() - ss.data_ts >= interval - 0.5)):
    with st.spinner("Updating market data…"):
        load_market(demo, symbol, expiry)

orders = paper.load_orders()
positions, events = paper.build_positions(orders, ss.marks)
auto_exits = paper.check_brackets(positions, paper.load_brackets())
for p, status in auto_exits:
    side = "SELL" if p["Net Qty"] > 0 else "BUY"
    paper.place_order(p["Index"], p["Expiry"], p["Type"], p["Strike"], side, abs(p["Net Qty"]), p["LTP"], status=status)
    st.toast(f"{status}: {p['Index']} {p['Strike']:g} {p['Type']} closed at {inr(p['LTP'])}", icon="🎯" if status == "TARGET HIT" else "🛑")
if auto_exits:
    orders = paper.load_orders()
    positions, events = paper.build_positions(orders, ss.marks)
brackets = paper.load_brackets()

realized = positions["Realized"].sum() if not positions.empty else 0.0
unreal = pd.to_numeric(positions["Unrealized"], errors="coerce").fillna(0).sum() if not positions.empty else 0.0
net = realized + unreal
stats = paper.trade_stats(events)
chain, spot = ss.chain, ss.spot
live_ok = not chain.empty and spot is not None
cs = chain_stats(chain, spot) if live_ok else None


# ------------------------------ actions ------------------------------
def close_position(p, status="SQUARE OFF"):
    side = "SELL" if p["Net Qty"] > 0 else "BUY"
    paper.place_order(p["Index"], p["Expiry"], p["Type"], p["Strike"], side, abs(p["Net Qty"]), p["LTP"], status=status)


@st.dialog("Place paper order")
def trade_dialog(sym, exp, typ, strike, ltp, side0):
    lot = lot_of(sym)
    held = positions[(positions["Index"] == sym) & (positions["Expiry"] == exp) & (positions["Type"] == typ)
                     & (positions["Strike"] == float(strike))]["Net Qty"].sum() if not positions.empty else 0
    st.markdown(f"#### {sym} {strike:g} {typ}")
    st.caption(f"Expiry {exp} · fills at {inr(ltp)} (LTP at {ss.fetched_at:%H:%M:%S})")
    side = st.radio("Action", ["BUY", "SELL"], horizontal=True, key="dlg_side", label_visibility="collapsed")
    lots = st.number_input(f"Lots (1 lot = {lot} qty)", 1, 1000, key="dlg_lots")
    qty = int(lots) * lot
    value = ltp * qty
    c1, c2 = st.columns(2)
    c1.metric("Quantity", f"{qty:,}")
    c2.metric("You pay" if side == "BUY" else "You receive", inr(value))
    if held:
        st.caption(f"Current position: {'long' if held > 0 else 'short'} {abs(held):,}. "
                   f"This order will {'reduce' if (held > 0) != (side == 'BUY') else 'add to'} it.")
    use_b = st.checkbox("Set stop-loss & target", key="dlg_bracket")
    sl_pct = tg_pct = None
    if use_b:
        b1, b2 = st.columns(2)
        sl_pct = b1.number_input("Stop-loss %", 1, 99, key="dlg_sl")
        tg_pct = b2.number_input("Target %", 1, 500, key="dlg_tg")
        sgn = 1 if side == "BUY" else -1
        st.caption(f"Stop-loss {inr(ltp * (1 - sgn * sl_pct / 100))} · Target {inr(max(ltp * (1 + sgn * tg_pct / 100), .05))}"
                   " — exits automatically when price touches these.")
    a, b = st.columns([2, 1])
    if a.button(f"Confirm {side} · {int(lots)} lot{'s' if lots > 1 else ''}", type="primary", key="dlg_ok"):
        paper.place_order(sym, exp, typ, strike, side, qty, ltp, sl_pct=sl_pct, tg_pct=tg_pct)
        ss.dialog_ts = 0.0
        st.toast(f"{side} {qty} {sym} {strike:g} {typ} @ {inr(ltp)}", icon="✅")
        st.rerun()
    if b.button("Cancel", key="dlg_cancel"):
        ss.dialog_ts = 0.0
        st.rerun()


def open_trade(sym, exp, typ, strike, ltp, side="BUY"):
    ss.update(dlg_side=side, dlg_lots=1, dlg_bracket=False, dlg_sl=30, dlg_tg=50, dialog_ts=time.time())
    trade_dialog(sym, exp, typ, strike, ltp, side)


def positions_panel(key):
    open_pos = positions[positions["Net Qty"] != 0] if not positions.empty else positions
    if open_pos.empty:
        st.info("No open positions yet. Click any price in the option chain to place a trade.")
        return
    recs = open_pos.to_dict("records")
    closable = [p for p in recs if p["LTP"] is not None and not pd.isna(p["LTP"])]
    if len(closable) > 1 and st.button("Square off all", key=f"{key}_all"):
        for p in closable:
            close_position(p)
        st.toast(f"Closed {len(closable)} positions", icon="✅")
        st.rerun()
    for p in recs:
        k = (p["Index"], p["Expiry"], p["Type"], float(p["Strike"]))
        lots = abs(p["Net Qty"]) / lot_of(p["Index"])
        sl_tg = brackets.get(k)
        tot = p["Total"]
        with st.container(border=True):
            a, b = st.columns([3, 2])
            a.markdown(f"**{p['Index']} {p['Strike']:g} {p['Type']}**  \n<span class='muted'>"
                       f"{'Long' if p['Net Qty'] > 0 else 'Short'} {abs(p['Net Qty']):,} ({lots:g} lot) · avg {inr(p['Avg'])} → "
                       f"{inr(p['LTP'])}{'' if p['Expiry'] == expiry else ' · ' + p['Expiry']}"
                       f"{f' · SL {inr(sl_tg[0])} / TGT {inr(sl_tg[1])}' if sl_tg else ''}</span>", unsafe_allow_html=True)
            b.markdown(f"<div class='num {cls(tot)}' style='font-size:1.25rem;font-weight:700;text-align:right'>"
                       f"{inr(tot, signed=True)}</div>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            ok = p["LTP"] is not None and not pd.isna(p["LTP"])
            if c1.button("Close", key=f"{key}_c_{k}", disabled=not ok, help=None if ok else "Load this expiry to close it"):
                close_position(p)
                st.toast(f"Closed {p['Index']} {p['Strike']:g} {p['Type']}", icon="✅")
                st.rerun()
            if c2.button("Add / reverse", key=f"{key}_t_{k}", disabled=not ok):
                open_trade(p["Index"], p["Expiry"], p["Type"], p["Strike"], p["LTP"], "BUY" if p["Net Qty"] > 0 else "SELL")


# ------------------------------ header ------------------------------
h1, h2 = st.columns([3, 2])
with h1:
    badge = "<span class='badge demo'>DEMO</span>" if demo else "<span class='badge live'>LIVE NSE</span>"
    st.markdown(f"## {symbol} {badge}", unsafe_allow_html=True)
    if live_ok:
        st.markdown(f"<span class='num' style='font-size:1.5rem;font-weight:600'>{spot:,.2f}</span> "
                    f"<span class='muted'>spot · {expiry} · updated {ss.fetched_at:%H:%M:%S}"
                    f"{' · simulated prices' if demo else ''}</span>", unsafe_allow_html=True)
if ss.error:
    st.error(f"Market data error: {ss.error}")
    if not demo:
        st.caption("NSE often blocks automated requests. Retry in a minute, or switch **Data source → Demo** in the sidebar to keep practising.")

with h2:
    st.markdown(f"<div class='hero' style='text-align:right'><div class='lbl'>Net P&L</div>"
                f"<div class='big {cls(net)}'>{inr(net, signed=True)}</div>"
                f"<div class='muted num'>{net / capital * 100:+.2f}% on {inr(capital, 0)} · equity {inr(capital + net, 0)}</div></div>",
                unsafe_allow_html=True)

m = st.columns(5)
m[0].metric("Realized", inr(realized, signed=True))
m[1].metric("Unrealized", inr(unreal, signed=True))
m[2].metric("Win rate", f"{stats['win']:.0f}%" if stats["win"] is not None else "—", f"{stats['n']} closed trades", delta_color="off")
m[3].metric("Open positions", int((positions["Net Qty"] != 0).sum()) if not positions.empty else 0)
m[4].metric("PCR (OI)", f"{cs['pcr']:.2f}" if cs and cs["pcr"] else "—",
            f"Max pain {cs['maxpain']:,.0f}" if cs else None, delta_color="off")

page = st.segmented_control("Page", ["📈 Trade", "💰 P&L dashboard", "📒 Orders", "🔬 Market"], default="📈 Trade",
                            key="page", label_visibility="collapsed") or "📈 Trade"


# ------------------------------ pages ------------------------------
def page_trade():
    left, right = st.columns([3, 1.35], gap="large")
    with left:
        if not live_ok:
            st.warning("Option chain isn't loaded yet. Press **Refresh now**, or switch to Demo data in the sidebar.")
            return
        d = cs["d"]
        i = int(np.abs(d["STRIKE"].values - cs["atm"]).argmin())
        w = d.iloc[max(0, i - window): i + window + 1]
        mx = max(w["CE OI"].max(), w["PE OI"].max(), 1)
        st.caption("Click a price to open the Buy/Sell pop-up. Bars show open interest (OI) with change in OI.")
        with st.container(key="ladder"):
            hc = st.columns([1.5, 1, 0.9, 1, 1.5])
            for c, t in zip(hc, ["CALL OI", "CALL price", "Strike", "PUT price", "PUT OI"]):
                c.markdown(f"<div class='muted' style='text-align:center'>{t}</div>", unsafe_allow_html=True)
            spot_drawn = False
            for _, r in w.iterrows():
                k = float(r["STRIKE"])
                if not spot_drawn and k > spot:
                    st.markdown(f"<div class='spotline'>spot {spot:,.2f}</div>", unsafe_allow_html=True)
                    spot_drawn = True
                c = st.columns([1.5, 1, 0.9, 1, 1.5])
                for col, typ, side in ((c[0], "CE", "right"), (c[4], "PE", "left")):
                    oi, chg = r[f"{typ} OI"], r[f"{typ} CHNG IN OI"]
                    pct = 0 if pd.isna(oi) else min(oi / mx * 100, 100)
                    itm = (k < spot) if typ == "CE" else (k > spot)
                    tint = "rgba(128,128,128,.10)" if itm else "transparent"
                    colr = "rgba(229,72,77,.28)" if typ == "CE" else "rgba(14,159,110,.28)"
                    direction = "270deg" if side == "right" else "90deg"
                    chg_col = GREEN if (chg or 0) >= 0 else RED
                    col.markdown(f"<div class='oi' style='text-align:{side};background:linear-gradient({direction},{colr} {pct:.0f}%,{tint} {pct:.0f}%)'>"
                                 f"{short(oi)} <span style='color:{chg_col};font-size:.72rem'>{'+' if (chg or 0) >= 0 else ''}{short(chg)}</span></div>",
                                 unsafe_allow_html=True)
                c[2].markdown(f"<div class='strike {'atm' if k == cs['atm'] else ''}'>{k:g}</div>", unsafe_allow_html=True)
                for col, typ in ((c[1], "CE"), (c[3], "PE")):
                    ltp = r[f"{typ} LTP"]
                    if col.button("—" if pd.isna(ltp) else f"{ltp:,.2f}", key=f"b_{typ}_{k}", disabled=pd.isna(ltp),
                                  help=f"Trade {symbol} {k:g} {typ}"):
                        open_trade(symbol, expiry, typ, k, float(ltp))
    with right:
        st.markdown("##### Open positions")
        positions_panel("trade")


def page_pnl():
    c1, c2 = st.columns([1.7, 1])
    with c1:
        st.markdown("##### Profit / loss over time")
        if events.empty and unreal == 0:
            st.info("Close a trade to start your P&L curve.")
        else:
            cum = events["pnl"].cumsum().tolist() if not events.empty else []
            xs = ["Start"] + [t.replace("T", " ")[5:16] for t in events["time"]] + ["Now"]
            ys = [0] + cum + [(cum[-1] if cum else 0) + unreal]
            col = GREEN if ys[-1] >= 0 else RED
            fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers", line=dict(color=col, width=2.5, shape="hv"),
                                       fill="tozeroy", fillcolor="rgba(14,159,110,.12)" if ys[-1] >= 0 else "rgba(229,72,77,.12)",
                                       hovertemplate="%{x}<br>₹%{y:,.2f}<extra></extra>"))
            st.plotly_chart(fig_style(fig, 300), key="eq")
    with c2:
        st.markdown("##### Trade stats")
        s = stats
        rows = [("Closed trades", s["n"]), ("Win rate", f"{s['win']:.0f}%" if s["win"] is not None else "—"),
                ("Best trade", inr(s["best"], signed=True)), ("Worst trade", inr(s["worst"], signed=True)),
                ("Avg win", inr(s["avg_win"], signed=True)), ("Avg loss", inr(s["avg_loss"], signed=True)),
                ("Max drawdown", inr(s["dd"]))]
        st.markdown("".join(f"<div style='display:flex;justify-content:space-between;padding:.28rem 0;border-bottom:1px solid rgba(128,128,128,.18)'>"
                            f"<span class='muted'>{a}</span><span class='num'><b>{b}</b></span></div>" for a, b in rows),
                    unsafe_allow_html=True)

    st.markdown("##### Open positions & payoff at expiry")
    p1, p2 = st.columns([1, 1.6], gap="large")
    with p1:
        positions_panel("pnl")
    with p2:
        mine = [p for p in positions[positions["Net Qty"] != 0].to_dict("records")
                if p["Index"] == symbol and p["Expiry"] == expiry] if not positions.empty else []
        if not mine or not live_ok:
            st.info(f"Payoff chart appears when you hold {symbol} {expiry} options.")
        else:
            xs, ys = paper.payoff_curve(mine, spot)
            fig = go.Figure()
            fig.add_scatter(x=xs, y=np.where(ys >= 0, ys, 0), fill="tozeroy", line=dict(width=0), fillcolor="rgba(14,159,110,.30)", hoverinfo="skip", showlegend=False)
            fig.add_scatter(x=xs, y=np.where(ys <= 0, ys, 0), fill="tozeroy", line=dict(width=0), fillcolor="rgba(229,72,77,.30)", hoverinfo="skip", showlegend=False)
            fig.add_scatter(x=xs, y=ys, line=dict(color="#888", width=2), name="P&L at expiry", hovertemplate="%{x:,.0f}: ₹%{y:,.0f}<extra></extra>")
            fig.add_vline(x=spot, line_dash="dash", line_color=AMBER, annotation_text=f"spot {spot:,.0f}")
            st.plotly_chart(fig_style(fig, 330), key="payoff")
            be = paper.breakevens(xs, ys)
            st.caption(f"Breakeven: {', '.join(f'{b:,.0f}' for b in be) or 'none in ±7% range'} · "
                       f"best in range {inr(ys.max(), 0, True)} · worst in range {inr(ys.min(), 0)}")
    if not positions.empty:
        st.markdown("##### P&L by contract")
        lab = positions["Index"] + " " + positions["Strike"].map(lambda x: f"{x:g}") + " " + positions["Type"]
        tot = positions["Total"].astype(float).fillna(0)
        fig = go.Figure(go.Bar(x=lab, y=tot, marker_color=[GREEN if v >= 0 else RED for v in tot],
                               hovertemplate="%{x}<br>₹%{y:,.2f}<extra></extra>"))
        st.plotly_chart(fig_style(fig, 260), key="bycontract")


def page_orders():
    if orders.empty:
        st.info("Executed paper orders will appear here.")
    else:
        f1, f2 = st.columns(2)
        sides = f1.multiselect("Side", ["BUY", "SELL"], ["BUY", "SELL"])
        stat = f2.multiselect("Status", sorted(orders["status"].unique()), sorted(orders["status"].unique()))
        v = orders[orders["side"].isin(sides) & orders["status"].isin(stat)].copy()
        v["order_time"] = pd.to_datetime(v["order_time"]).dt.strftime("%d-%b %H:%M:%S")
        st.dataframe(v.rename(columns={"order_time": "Time", "symbol": "Index", "expiry": "Expiry", "option_type": "Type",
                                       "strike": "Strike", "side": "Side", "quantity": "Qty", "price": "Price", "status": "Status"}),
                     hide_index=True, column_config={"Price": st.column_config.NumberColumn(format="₹%.2f")})
        st.download_button("⬇️ Download order history (CSV)", orders.to_csv(index=False).encode(), "paper_order_history.csv", "text/csv")
    with st.popover("Reset paper account"):
        st.warning("This deletes all orders, positions and stop-loss/target settings.")
        if st.button("Yes, delete everything", type="primary"):
            paper.reset_account()
            ss.marks = {}
            st.rerun()


def page_market():
    if not live_ok:
        st.warning("Load the option chain first.")
        return
    d = cs["d"]
    k = st.columns(5)
    k[0].metric("ATM strike", f"{cs['atm']:,.0f}")
    k[1].metric("ATM straddle", inr(cs["straddle"]), f"±{cs['straddle'] / spot * 100:.2f}% implied move", delta_color="off")
    k[2].metric("Resistance (max call OI)", f"{cs['res']:,.0f}")
    k[3].metric("Support (max put OI)", f"{cs['sup']:,.0f}")
    k[4].metric("Max pain", f"{cs['maxpain']:,.0f}")
    i = int(np.abs(d["STRIKE"].values - cs["atm"]).argmin())
    w = d.iloc[max(0, i - window - 5): i + window + 6]
    a, b = st.columns(2)
    with a:
        st.markdown("##### Open interest")
        fig = go.Figure([go.Bar(x=w["STRIKE"], y=w["CE OI"], name="Call OI", marker_color=RED),
                         go.Bar(x=w["STRIKE"], y=w["PE OI"], name="Put OI", marker_color=GREEN)])
        fig.add_vline(x=spot, line_dash="dash", line_color=AMBER)
        st.plotly_chart(fig_style(fig).update_layout(barmode="group"), key="oi")
    with b:
        st.markdown("##### Change in OI")
        fig = go.Figure([go.Bar(x=w["STRIKE"], y=w["CE CHNG IN OI"], name="Call", marker_color=RED),
                         go.Bar(x=w["STRIKE"], y=w["PE CHNG IN OI"], name="Put", marker_color=GREEN)])
        fig.add_vline(x=spot, line_dash="dash", line_color=AMBER)
        st.plotly_chart(fig_style(fig).update_layout(barmode="group"), key="coi")
    a, b = st.columns(2)
    with a:
        st.markdown("##### Implied volatility smile")
        fig = go.Figure([go.Scatter(x=w["STRIKE"], y=w["CE IV"].replace(0, np.nan), name="Call IV", line=dict(color=RED)),
                         go.Scatter(x=w["STRIKE"], y=w["PE IV"].replace(0, np.nan), name="Put IV", line=dict(color=GREEN))])
        st.plotly_chart(fig_style(fig), key="iv")
    with b:
        st.markdown("##### Max pain curve")
        pn = cs["pain"].loc[w["STRIKE"].min():w["STRIKE"].max()]
        fig = go.Figure(go.Scatter(x=pn.index, y=pn.values, line=dict(color="#888"), fill="tozeroy"))
        fig.add_vline(x=cs["maxpain"], line_dash="dot", line_color=AMBER, annotation_text="max pain")
        st.plotly_chart(fig_style(fig), key="pain")
    st.download_button("⬇️ Download option chain (CSV)", chain.to_csv(index=False).encode(),
                       f"{symbol}_{expiry}_option_chain.csv", "text/csv")


{"📈 Trade": page_trade, "💰 P&L dashboard": page_pnl, "📒 Orders": page_orders, "🔬 Market": page_market}[page]()

# ------------------------------ auto refresh ------------------------------
if auto and expiry:
    @st.fragment(run_every=2)
    def _ticker():
        if not dialog_open() and time.time() - ss.data_ts >= interval:
            st.rerun()
    _ticker()

st.caption("Paper trading only - no orders are sent to any broker. Fills are simulated at the displayed LTP; brokerage, taxes, slippage and "
           "margin are not modelled. NSE website endpoints are unofficial and can change or block requests without notice.")
