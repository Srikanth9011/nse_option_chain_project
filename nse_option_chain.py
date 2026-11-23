"""NSE option-chain fetching (live endpoint). Unofficial endpoint - may be blocked or change."""
import time
from datetime import datetime

import pandas as pd
import requests

BASE_URL = "https://www.nseindia.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

COLUMNS = [
    "CE OI", "CE CHNG IN OI", "CE VOLUME", "CE IV", "CE LTP",
    "CE CHNG", "CE BID QTY", "CE BID", "CE ASK", "CE ASK QTY",
    "STRIKE",
    "PE BID QTY", "PE BID", "PE ASK", "PE ASK QTY", "PE CHNG",
    "PE LTP", "PE IV", "PE VOLUME", "PE CHNG IN OI", "PE OI",
]

_cache = {"session": None, "t": 0.0}


def _session(symbol, fresh=False):
    """Reuse one cookie-warmed session for a few minutes (fewer hits = fewer blocks)."""
    if fresh or _cache["session"] is None or time.time() - _cache["t"] > 240:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{BASE_URL}/option-chain?symbol={symbol.upper()}",
        })
        s.get(BASE_URL, timeout=10)
        _cache["session"], _cache["t"] = s, time.time()
    return _cache["session"]


def fetch_expiries(symbol="NIFTY"):
    """Return the list of expiry strings (DD-Mon-YYYY) NSE currently lists for an index."""
    s = _session(symbol, fresh=True)
    r = s.get(f"{BASE_URL}/api/option-chain-contract-info?symbol={symbol.upper()}", timeout=15)
    r.raise_for_status()
    dates = r.json().get("expiryDates", [])
    if not dates:
        raise ValueError("NSE returned no expiry dates.")
    return dates


def fetch_option_chain(symbol="NIFTY", expiry="23-Nov-2026", retries=3, pause=2):
    """Fetch NSE option-chain data and return (dataframe, raw_json)."""
    last_error = None
    for attempt in range(retries):
        try:
            s = _session(symbol, fresh=attempt > 0)
            url = (f"{BASE_URL}/api/option-chain-v3?type=Indices"
                   f"&symbol={symbol.upper()}&expiry={expiry}")
            response = s.get(url, timeout=15)
            response.raise_for_status()
            payload = response.json()

            records = payload.get("records", {}).get("data", [])
            if not records:
                raise ValueError(
                    "NSE returned no option-chain rows. Check the symbol/expiry "
                    "or whether NSE has published contracts for that expiry."
                )

            rows = []
            for item in records:
                ce = item.get("CE") or {}
                pe = item.get("PE") or {}
                rows.append([
                    ce.get("openInterest"), ce.get("changeinOpenInterest"),
                    ce.get("totalTradedVolume"), ce.get("impliedVolatility"),
                    ce.get("lastPrice"), ce.get("change"),
                    ce.get("bidQty"), ce.get("bidprice"),
                    ce.get("askPrice"), ce.get("askQty"),
                    item.get("strikePrice"),
                    pe.get("bidQty"), pe.get("bidprice"),
                    pe.get("askPrice"), pe.get("askQty"),
                    pe.get("change"), pe.get("lastPrice"),
                    pe.get("impliedVolatility"), pe.get("totalTradedVolume"),
                    pe.get("changeinOpenInterest"), pe.get("openInterest"),
                ])
            df = pd.DataFrame(rows, columns=COLUMNS)
            df = df.sort_values("STRIKE").reset_index(drop=True)
            return df, payload

        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(pause * (attempt + 1))

    raise RuntimeError(
        f"Could not fetch NSE option-chain data after {retries} attempts: "
        f"{last_error}. NSE may block automated requests or require a fresh session."
    )


def extract_spot(payload, df):
    """Underlying index value from the payload; falls back to put-call parity estimate."""
    try:
        v = payload.get("records", {}).get("underlyingValue")
        if v:
            return float(v)
    except (TypeError, ValueError):
        pass
    d = df[["STRIKE", "CE LTP", "PE LTP"]].apply(pd.to_numeric, errors="coerce").dropna()
    if d.empty:
        return float(df["STRIKE"].median())
    return float(d.loc[(d["CE LTP"] - d["PE LTP"]).abs().idxmin(), "STRIKE"])


def save_csv(df, symbol="NIFTY"):
    filename = f"{symbol.upper()}_option_chain_{datetime.now():%Y%m%d_%H%M%S}.csv"
    df.to_csv(filename, index=False)
    return filename
