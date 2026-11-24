"""Paper-trading storage (SQLite) and the P&L engine. Fully compatible with the old orders table."""
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DB = Path(os.environ.get("PAPER_DB", Path(__file__).with_name("paper_trading.db")))
DB_PATH = BASE_DB


def set_mode(demo):
    """Live and Demo trades live in separate database files so simulated prices never touch real-data history."""
    global DB_PATH
    DB_PATH = BASE_DB.with_name(BASE_DB.stem + "_demo" + BASE_DB.suffix) if demo else BASE_DB

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_time TEXT NOT NULL, symbol TEXT NOT NULL,
        expiry TEXT NOT NULL, option_type TEXT NOT NULL, strike REAL NOT NULL, side TEXT NOT NULL,
        quantity INTEGER NOT NULL, price REAL NOT NULL, status TEXT NOT NULL DEFAULT 'EXECUTED')""",
    """CREATE TABLE IF NOT EXISTS brackets (
        symbol TEXT, expiry TEXT, option_type TEXT, strike REAL, sl REAL, target REAL,
        PRIMARY KEY (symbol, expiry, option_type, strike))""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS marks (
        symbol TEXT, expiry TEXT, option_type TEXT, strike REAL, ltp REAL, ts TEXT,
        PRIMARY KEY (symbol, expiry, option_type, strike))""",
    "CREATE TABLE IF NOT EXISTS spots (symbol TEXT, expiry TEXT, spot REAL, ts TEXT, PRIMARY KEY (symbol, expiry))",
]


def is_expired(expiry, now=None):
    """Index options stop trading at 15:30 IST on expiry day."""
    try:
        return datetime.strptime(expiry, "%d-%b-%Y").replace(hour=15, minute=30) < (now or datetime.now())
    except ValueError:
        return False


def save_market(symbol, expiry, spot, rows):
    """Persist last seen prices (rows = [(type, strike, ltp)]) and spot so P&L and expiry settlement survive restarts."""
    ts = datetime.now().isoformat(timespec="seconds")
    with closing(sqlite3.connect(DB_PATH)) as c:
        for s in SCHEMA:
            c.execute(s)
        c.executemany("INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?,?)",
                      [(symbol, expiry, t, float(k), float(v), ts) for t, k, v in rows])
        c.execute("INSERT OR REPLACE INTO spots VALUES (?,?,?,?)", (symbol, expiry, float(spot), ts))
        c.commit()


def load_marks():
    return {(r[0], r[1], r[2], float(r[3])): r[4] for r in _run("SELECT symbol, expiry, option_type, strike, ltp FROM marks", fetch=True)}


def load_spots():
    return {(r[0], r[1]): (r[2], r[3]) for r in _run("SELECT symbol, expiry, spot, ts FROM spots", fetch=True)}


def settle_price(option_type, strike, spot):
    """Index options are cash-settled at intrinsic value."""
    return max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)


def settle_position(p, spot):
    side = "SELL" if p["Net Qty"] > 0 else "BUY"
    place_order(p["Index"], p["Expiry"], p["Type"], p["Strike"], side, abs(p["Net Qty"]),
                settle_price(p["Type"], p["Strike"], spot), status="EXPIRED")


def auto_settle(positions, spots):
    """Settle expired positions when the last recorded spot was captured on expiry day. Returns number settled."""
    n = 0
    for p in positions[(positions["Net Qty"] != 0) & positions["Expired"]].to_dict("records"):
        sp = spots.get((p["Index"], p["Expiry"]))
        if sp and sp[1][:10] == datetime.strptime(p["Expiry"], "%d-%b-%Y").date().isoformat():
            settle_position(p, sp[0])
            n += 1
    return n


def _run(sql, params=(), fetch=False):
    with closing(sqlite3.connect(DB_PATH)) as c:
        for s in SCHEMA:
            c.execute(s)
        cur = c.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        c.commit()
        return rows


def get_setting(key, default):
    r = _run("SELECT value FROM settings WHERE key=?", (key,), fetch=True)
    return type(default)(r[0][0]) if r else default


def set_setting(key, value):
    _run("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))


def load_orders():
    with closing(sqlite3.connect(DB_PATH)) as c:
        for s in SCHEMA:
            c.execute(s)
        return pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", c)


def load_brackets():
    rows = _run("SELECT symbol, expiry, option_type, strike, sl, target FROM brackets", fetch=True)
    return {(r[0], r[1], r[2], float(r[3])): (r[4], r[5]) for r in rows}


def reset_account():
    for t in ("orders", "brackets"):
        _run(f"DELETE FROM {t}")


def _net_qty(symbol, expiry, otype, strike):
    rows = _run("SELECT side, quantity FROM orders WHERE symbol=? AND expiry=? AND option_type=? AND strike=?",
                (symbol, expiry, otype, strike), fetch=True)
    return sum(q if s == "BUY" else -q for s, q in rows)


def place_order(symbol, expiry, otype, strike, side, qty, price, status="EXECUTED", sl_pct=None, tg_pct=None):
    """Record a simulated fill. Optional SL/target (in % of fill price) attach to the resulting position."""
    strike = float(strike)
    _run("INSERT INTO orders (order_time, symbol, expiry, option_type, strike, side, quantity, price, status) "
         "VALUES (?,?,?,?,?,?,?,?,?)",
         (datetime.now().isoformat(timespec="seconds"), symbol, expiry, otype, strike, side, int(qty),
          float(price), status))
    net = _net_qty(symbol, expiry, otype, strike)
    key = (symbol, expiry, otype, strike)
    if net == 0:
        _run("DELETE FROM brackets WHERE symbol=? AND expiry=? AND option_type=? AND strike=?", key)
    elif sl_pct is not None and tg_pct is not None:
        sgn = 1 if net > 0 else -1
        sl = round(price * (1 - sgn * sl_pct / 100), 2)
        tg = round(max(price * (1 + sgn * tg_pct / 100), 0.05), 2)
        _run("INSERT OR REPLACE INTO brackets VALUES (?,?,?,?,?,?)", (*key, sl, tg))


# ------------------------------ P&L engine ------------------------------
def build_positions(orders, marks):
    """Replay the order ledger. Returns (positions_df, closed_trade_events_df)."""
    cols = ["Index", "Expiry", "Type", "Strike", "Net Qty", "Avg", "LTP", "Realized", "Unrealized", "Total", "Expired"]
    if orders.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=["time", "label", "pnl"])
    states, events = {}, []
    for o in orders.sort_values("id").itertuples():
        key = (o.symbol, o.expiry, o.option_type, float(o.strike))
        st = states.setdefault(key, {"q": 0, "avg": 0.0, "real": 0.0})
        tq = (1 if o.side == "BUY" else -1) * int(o.quantity)
        px, oq = float(o.price), st["q"]
        if oq == 0 or oq * tq > 0:
            nq = oq + tq
            st["avg"] = (abs(oq) * st["avg"] + abs(tq) * px) / abs(nq)
            st["q"] = nq
        else:
            closed = min(abs(oq), abs(tq))
            pnl = (px - st["avg"]) * closed if oq > 0 else (st["avg"] - px) * closed
            st["real"] += pnl
            events.append({"time": o.order_time, "label": f"{o.symbol} {key[3]:g} {o.option_type}", "pnl": pnl})
            nq = oq + tq
            if nq == 0:
                st["avg"] = 0.0
            elif oq * nq < 0:
                st["avg"] = px
            st["q"] = nq
    rows = []
    for (sym, exp, typ, k), st in states.items():
        ltp = marks.get((sym, exp, typ, k))
        expired = is_expired(exp)
        unreal = (ltp - st["avg"]) * st["q"] if (ltp is not None and st["q"] and not expired) else (0.0 if st["q"] == 0 else None)
        rows.append([sym, exp, typ, k, st["q"], st["avg"], ltp, st["real"], unreal,
                     st["real"] + (unreal or 0.0), expired])
    pos = pd.DataFrame(rows, columns=cols).sort_values(["Index", "Expiry", "Strike", "Type"]).reset_index(drop=True)
    return pos, pd.DataFrame(events, columns=["time", "label", "pnl"])


def check_brackets(positions, brackets):
    """Return list of (position_row, status) whose stop-loss or target has been touched."""
    hits = []
    for p in positions[positions["Net Qty"] != 0].to_dict("records"):
        b = brackets.get((p["Index"], p["Expiry"], p["Type"], float(p["Strike"])))
        if not b or p["LTP"] is None or pd.isna(p["LTP"]) or p["Expired"]:
            continue
        sl, tg = b
        long_ = p["Net Qty"] > 0
        if (long_ and p["LTP"] <= sl) or (not long_ and p["LTP"] >= sl):
            hits.append((p, "SL HIT"))
        elif (long_ and p["LTP"] >= tg) or (not long_ and p["LTP"] <= tg):
            hits.append((p, "TARGET HIT"))
    return hits


def trade_stats(events):
    if events.empty:
        return dict(n=0, win=None, best=None, worst=None, avg_win=None, avg_loss=None, dd=0.0)
    p = events["pnl"]
    wins, losses = p[p > 0], p[p < 0]
    cum = p.cumsum()
    return dict(n=len(p), win=len(wins) / len(p) * 100, best=p.max(), worst=p.min(),
                avg_win=wins.mean() if len(wins) else None, avg_loss=losses.mean() if len(losses) else None,
                dd=float((cum - cum.cummax().clip(lower=0)).min()))


def payoff_curve(open_pos, spot, lo_pct=0.07):
    """Expiry payoff of open positions (same index+expiry) across a spot range."""
    xs = np.linspace(spot * (1 - lo_pct), spot * (1 + lo_pct), 241)
    ys = np.zeros_like(xs)
    for p in open_pos:
        intrinsic = np.maximum(xs - p["Strike"], 0) if p["Type"] == "CE" else np.maximum(p["Strike"] - xs, 0)
        ys += (intrinsic - p["Avg"]) * p["Net Qty"]
    return xs, ys


def breakevens(xs, ys):
    out = []
    for i in range(len(xs) - 1):
        if ys[i] == 0:
            out.append(xs[i])
        elif ys[i] * ys[i + 1] < 0:
            out.append(xs[i] - ys[i] * (xs[i + 1] - xs[i]) / (ys[i + 1] - ys[i]))
    return out
