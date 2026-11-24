"""Simulated option chain (Black-Scholes) so the dashboard works anywhere, any time."""
import math
import zlib
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from nse_option_chain import COLUMNS

START_SPOT = {"NIFTY": 25350.0, "BANKNIFTY": 57500.0, "FINNIFTY": 27000.0}
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
RATE = 0.065


def demo_expiries(n=5):
    d, out = datetime.now().date(), []
    while len(out) < n:
        if d.weekday() == 1 and datetime.now() < datetime.combine(d, datetime.min.time()).replace(hour=15, minute=30):  # Tuesday, not yet expired
            out.append(d.strftime("%d-%b-%Y"))
        d += timedelta(days=1)
    return out


def tick(state, symbol):
    """Random-walk the simulated spot one step and return it."""
    s = state.setdefault(symbol, {"spot": START_SPOT[symbol], "open": START_SPOT[symbol]})
    s["spot"] = round(s["spot"] * math.exp(np.random.normal(0, 0.00035)), 2)
    return s["spot"]


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs(spot, k, t, iv, call):
    if t <= 0 or iv <= 0:
        return max(spot - k, 0) if call else max(k - spot, 0)
    d1 = (math.log(spot / k) + (RATE + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if call:
        return spot * _ncdf(d1) - k * math.exp(-RATE * t) * _ncdf(d2)
    return k * math.exp(-RATE * t) * _ncdf(-d2) - spot * _ncdf(-d1)


def _tick_round(p):
    return max(round(round(p / 0.05) * 0.05, 2), 0.05)


def build_chain(state, symbol, expiry):
    spot, open_spot = state[symbol]["spot"], state[symbol]["open"]
    step = STEP[symbol]
    exp_dt = datetime.strptime(expiry, "%d-%b-%Y") + timedelta(hours=15, minutes=30)
    t = max((exp_dt - datetime.now()).total_seconds(), 3600) / (365 * 24 * 3600)
    atm = round(START_SPOT[symbol] / step) * step
    rows = []
    for k in range(atm - 40 * step, atm + 41 * step, step):
        rng = np.random.default_rng(zlib.crc32(f"{symbol}{expiry}{k}".encode()))
        iv = 0.13 + 0.9 * (k / spot - 1) ** 2
        w = math.exp(-(((k - spot) / (spot * 0.035)) ** 2)) * (0.5 + rng.random())
        ce_oi = int(w * 4e5 * (1.7 if k > spot else 0.6) / 75) * 75
        pe_oi = int(w * 4e5 * (1.7 if k < spot else 0.6) / 75) * 75
        vals = {}
        for typ, call, oi in (("CE", True, ce_oi), ("PE", False, pe_oi)):
            ltp = _tick_round(_bs(spot, k, t, iv, call))
            ltp0 = _tick_round(_bs(open_spot, k, t, iv, call))
            vals[typ] = dict(oi=oi, coi=int(rng.normal(0, oi * 0.08)), vol=int(oi * rng.uniform(.1, .6)),
                             iv=round(iv * 100, 2), ltp=ltp, chg=round(ltp - ltp0, 2),
                             bq=int(rng.integers(1, 40)) * 75, bid=max(round(ltp - 0.05, 2), 0.05),
                             ask=round(ltp + 0.05, 2), aq=int(rng.integers(1, 40)) * 75)
        c, p = vals["CE"], vals["PE"]
        rows.append([c["oi"], c["coi"], c["vol"], c["iv"], c["ltp"], c["chg"], c["bq"], c["bid"], c["ask"], c["aq"],
                     float(k),
                     p["bq"], p["bid"], p["ask"], p["aq"], p["chg"], p["ltp"], p["iv"], p["vol"], p["coi"], p["oi"]])
    return pd.DataFrame(rows, columns=COLUMNS)
