"""
place_orders.py
Places paper trades (or real SAXO trades when LIVE_TRADING=true).
Saves open positions to data/positions.json

Runs at 09:00 Oslo Mon-Fri.

Environment variables:
  TURSO_URL     — Turso database URL
  TURSO_TOKEN   — Turso auth token
  LIVE_TRADING  — set to "true" to enable real SAXO trades (default: false)
  SAXO_TOKEN    — SAXO API OAuth2 token (only needed when LIVE_TRADING=true)
  SAXO_ACCOUNT  — SAXO account key (only needed when LIVE_TRADING=true)
"""

import os, json, urllib.request
from datetime import date, datetime, timezone

TURSO_URL    = os.environ["TURSO_URL"]
TURSO_TOKEN  = os.environ["TURSO_TOKEN"]
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").lower() == "true"
SAXO_TOKEN   = os.environ.get("SAXO_TOKEN", "")
SAXO_ACCOUNT = os.environ.get("SAXO_ACCOUNT", "")

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
STOP_LOSS     = 0.03  # 3%
HOLD_DAYS     = 3     # trading days to hold
MAX_POSITIONS = 10    # top N signals by divergence score (None = all)

# SAXO API base — SIM for paper testing, LIVE for real money
SAXO_BASE = "https://gateway.saxobank.com/sim/openapi" if not LIVE_TRADING \
            else "https://gateway.saxobank.com/openapi"


# ── Turso ─────────────────────────────────────────────────────────────────────

def turso(sql):
    url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
    req = urllib.request.Request(
        url,
        data=json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}},
                                       {"type": "close"}]}).encode(),
        headers={"Authorization": f"Bearer {TURSO_TOKEN}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    result = data["results"][0]["response"]["result"]
    cols   = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [v.get("value") for v in row])) for row in result["rows"]]


# ── Price fetching ────────────────────────────────────────────────────────────

def get_price_paper(ticker):
    """Paper: use Day 0 closing price from Turso."""
    today = date.today().isoformat()
    rows  = turso(f"""
        SELECT close FROM stock_prices
        WHERE ticker = '{ticker}' AND date <= '{today}'
        ORDER BY date DESC LIMIT 1
    """)
    return float(rows[0]["close"]) if rows and rows[0]["close"] else None


def get_saxo_uic(ticker):
    """
    Look up SAXO UIC (Unique Instrument Code) from Oslo Børs ticker.
    TODO: populate uic_map after SAXO API access is granted.
    Auto-lookup via SAXO instrument search as fallback.
    """
    uic_map = {
        # Add mappings here after SAXO API access, e.g.:
        # "EQNR.OL": "2281",
        # "DNB.OL":  "2560",
    }
    if ticker in uic_map:
        return uic_map[ticker]

    # Auto-lookup via SAXO instrument search API
    symbol = ticker.replace(".OL", "")
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/ref/v1/instruments?Keywords={symbol}&ExchangeId=OSE&AssetTypes=Stock",
            headers={"Authorization": f"Bearer {SAXO_TOKEN}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        instruments = data.get("Data", [])
        if instruments:
            uic = str(instruments[0]["Identifier"])
            print(f"  [SAXO] UIC for {ticker}: {uic}", flush=True)
            return uic
    except Exception as e:
        print(f"  [SAXO] UIC lookup failed for {ticker}: {e}", flush=True)
    return None


def get_price_live(ticker):
    """
    Live: get real-time ask price from SAXO API.
    Returns the current ask price (what you pay to buy).
    """
    if not SAXO_TOKEN:
        raise RuntimeError("SAXO_TOKEN not set")
    uic = get_saxo_uic(ticker)
    if not uic:
        return None
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/trade/v1/infoprices?Uic={uic}&AssetType=Stock",
            headers={"Authorization": f"Bearer {SAXO_TOKEN}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        ask = data.get("Quote", {}).get("Ask")
        return float(ask) if ask else None
    except Exception as e:
        print(f"  [SAXO] Price fetch failed for {ticker}: {e}", flush=True)
        return None


def get_current_price(ticker):
    """Route to paper or live price based on LIVE_TRADING flag."""
    if LIVE_TRADING:
        return get_price_live(ticker)
    return get_price_paper(ticker)


# ── SAXO order placement ──────────────────────────────────────────────────────

def place_saxo_order(ticker, shares):
    """
    Place a real market buy order via SAXO API.
    Returns order ID if successful, None otherwise.
    """
    if not SAXO_TOKEN or not SAXO_ACCOUNT:
        raise RuntimeError("SAXO_TOKEN and SAXO_ACCOUNT required for live trading")

    uic = get_saxo_uic(ticker)
    if not uic:
        return None

    order = {
        "Uic":         uic,
        "AssetType":   "Stock",
        "BuySell":     "Buy",
        "Amount":      round(shares, 0),  # whole shares only
        "OrderType":   "Market",
        "ManualOrder": False,
        "AccountKey":  SAXO_ACCOUNT,
    }
    try:
        req = urllib.request.Request(
            f"{SAXO_BASE}/trade/v2/orders",
            data=json.dumps(order).encode(),
            headers={"Authorization": f"Bearer {SAXO_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        order_id = data.get("OrderId")
        print(f"  [SAXO] Order placed: {ticker} — OrderId={order_id}", flush=True)
        return order_id
    except Exception as e:
        print(f"  [SAXO] Order failed for {ticker}: {e}", flush=True)
        return None


# ── Portfolio / position helpers ──────────────────────────────────────────────

def load_portfolio():
    path = os.path.join(DATA_DIR, "portfolio.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"cash": 100_000.0, "start_capital": 100_000.0,
            "created": date.today().isoformat()}


def save_portfolio(p):
    path = os.path.join(DATA_DIR, "portfolio.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(p, f, indent=2)


def load_positions():
    path = os.path.join(DATA_DIR, "positions.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_positions(p):
    path = os.path.join(DATA_DIR, "positions.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(p, f, indent=2)


def get_trading_days_ahead(from_date, n):
    rows = turso(f"""
        SELECT DISTINCT date FROM stock_prices
        WHERE date > '{from_date}'
        ORDER BY date ASC LIMIT {n}
    """)
    return rows[n-1]["date"] if len(rows) >= n else None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = date.today().isoformat()
    mode  = "LIVE 🔴" if LIVE_TRADING else "PAPER 📄"
    print(f"[ORDERS] {mode} — {today}", flush=True)

    # Load signals from last night
    sig_path = os.path.join(DATA_DIR, "signals_tonight.json")
    if not os.path.exists(sig_path):
        print("[ORDERS] No signals file — skipping", flush=True)
        return

    with open(sig_path) as f:
        sig_data = json.load(f)

    signals = sig_data.get("signals", [])
    if not signals:
        print("[ORDERS] No signals — no trades placed", flush=True)
        return

    # Skip if already traded today
    positions = load_positions()
    if any(p["entry_date"] == today and p["status"] == "open" for p in positions):
        print("[ORDERS] Already traded today — skipping", flush=True)
        return

    portfolio = load_portfolio()
    cash      = portfolio["cash"]
    if cash <= 0:
        print(f"[ORDERS] No cash (NOK {cash:,.0f}) — skipping", flush=True)
        return

    # Take top MAX_POSITIONS signals by divergence score
    if MAX_POSITIONS:
        signals = sorted(signals, key=lambda x: float(x.get("div_score") or 0), reverse=True)[:MAX_POSITIONS]

    n             = len(signals)
    position_size = cash / n
    exit_date     = get_trading_days_ahead(today, HOLD_DAYS)

    print(f"[ORDERS] {n} signals | NOK {position_size:,.0f} each | "
          f"exit {exit_date} | stop {STOP_LOSS*100:.0f}%", flush=True)

    new_positions  = []
    total_invested = 0.0

    for s in signals:
        ticker = s["ticker"]
        price  = get_current_price(ticker)

        if not price or price <= 0:
            print(f"  [SKIP] {ticker} — no price", flush=True)
            continue

        shares     = position_size / price
        invested   = shares * price
        stop_price = round(price * (1 - STOP_LOSS), 4)

        # Place real SAXO order if live
        order_id = None
        if LIVE_TRADING:
            order_id = place_saxo_order(ticker, shares)
            if not order_id:
                print(f"  [SKIP] {ticker} — SAXO order failed", flush=True)
                continue

        pos = {
            "id":            f"{ticker}_{today}",
            "ticker":        ticker,
            "name":          s.get("name", ""),
            "sector":        s.get("sector", ""),
            "entry_date":    today,
            "exit_date":     exit_date,
            "entry_price":   round(price, 4),
            "stop_price":    stop_price,
            "shares":        round(shares, 6),
            "invested":      round(invested, 2),
            "div_score":     s.get("div_score"),
            "sentiment":     s.get("sentiment"),
            "status":        "open",
            "exit_price":    None,
            "pnl":           None,
            "pnl_pct":       None,
            "exit_reason":   None,
            "live":          LIVE_TRADING,
            "saxo_order_id": order_id,
            "price_source":  "saxo_live" if LIVE_TRADING else "turso_close",
        }
        new_positions.append(pos)
        total_invested += invested

        flag = "🔴" if LIVE_TRADING else "📄"
        print(f"  {flag} BUY  {ticker:<12} {s['name'][:24]:<24} "
              f"NOK {price:.2f} × {shares:.2f} = NOK {invested:,.0f}  "
              f"stop={stop_price:.2f}", flush=True)

    if new_positions:
        portfolio["cash"] -= total_invested
        save_portfolio(portfolio)
        positions.extend(new_positions)
        save_positions(positions)
        print(f"\n[ORDERS] {len(new_positions)} trades placed. "
              f"Cash remaining: NOK {portfolio['cash']:,.0f}", flush=True)
    else:
        print("[ORDERS] No valid trades placed", flush=True)


if __name__ == "__main__":
    main()
