# NSE Option Chain — Paper Trading Dashboard (v2)

Streamlit dashboard: live NSE option chain + simulated Buy/Sell with a real-time Profit/Loss dashboard.
**Paper trading only — no orders are sent to any broker.**

## What's new in v2
- **Click-to-trade ladder** – click any CALL/PUT price → Buy/Sell pop-up (lots, BUY/SELL, optional stop-loss & target) → one-click confirm.
- **P&L dashboard** – net/realized/unrealized P&L, P&L-over-time curve, win rate, best/worst trade, max drawdown, P&L by contract, **payoff-at-expiry chart** with breakevens.
- **Open positions panel** – live P&L per position, one-click **Close**, **Square off all**, add/reverse.
- **Auto stop-loss / target exits** – checked on every refresh; shown in order history as `SL HIT` / `TARGET HIT`.
- **Auto-refresh** (pauses while a pop-up is open) and expiry dropdown loaded from NSE.
- **Market page** – PCR, max pain, support/resistance (max OI), ATM straddle (implied move), OI & change-in-OI charts, IV smile.
- **Demo mode** – simulated Black-Scholes chain with moving prices; works any time (market closed / NSE blocking).
- Spot price, ATM highlight, Indian-format rupees, order filters + CSV export, account reset.
- Your existing `paper_trading.db` keeps working (new tables are added automatically).

## Run
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
streamlit run app.py
```

## Using it
1. Sidebar → choose **Live NSE** or **Demo**, index and expiry.
2. **Trade** page → click a price → choose BUY/SELL and lots → Confirm.
3. Watch positions on the right; press **Close** to exit instantly.
4. **P&L dashboard** page → curves, stats and payoff chart.

## Files
| File | Purpose |
|---|---|
| `app.py` | Dashboard UI |
| `paper.py` | SQLite storage + P&L engine + SL/target logic |
| `nse_option_chain.py` | Live NSE fetch (option chain, expiry list, spot) |
| `demo_data.py` | Simulated option chain |
| `app_v1_backup.py`, `app_1.py` | Your earlier versions |

## Notes
- Lot sizes default to NIFTY 65 / BANKNIFTY 30 / FINNIFTY 60 — **verify against the latest NSE circular** and edit in the sidebar.
- Fills are at the displayed LTP. Brokerage, taxes, slippage and margin are not modelled.
- NSE website endpoints are unofficial and may block or change without notice. Don't use them as a production trading feed.
- Unrealized P&L for contracts in an expiry you haven't loaded in this session shows as "—" until you load that expiry.
