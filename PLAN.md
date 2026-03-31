# PLAN.md — Google Finance Bulk Trade Automator

## Executive Summary

This project automates bulk entry of stock trades (buy & sell) from Excel into Google Finance portfolios. The existing codebase is a strong foundation — FastAPI backend, Playwright browser automation, FIFO accounting, and a polished single-page frontend.

**After thorough codebase analysis, the two critical gaps are:**

1. **Input format mismatch** — User's Excel uses Stock Name + Exchange (e.g., `HDFC Bank | NSE`), but the system requires ISINs. A Stock Name resolver is needed.
2. **Native sell recording** — Google Finance has a dedicated "Record a sale" dialog (per lot, per stock). The current code FIFO-nets sells away before upload. Phase 2 will drive the real sell flow.

---

## Current State Audit

| Component | Status | What It Does |
|-----------|--------|--------------|
| `backend/main.py` | Complete | HTTP routes, upload, SSE streaming |
| `backend/automation.py` | Complete (~920 lines) | Playwright automation, 3-retry logic |
| `backend/excel_parser.py` | Complete | Parses `.xlsx` → `Trade` objects |
| `backend/fifo.py` | Complete | FIFO accounting, realized P&L |
| `backend/isin_resolver.py` | Complete | ISIN → NSE ticker via Groww API |
| `frontend/index.html` | Complete | Polished SPA, drag-drop, SSE log |
| `docs/SELECTORS.md` | Complete | Verified DOM selectors (2026-03-29) |
| `docs/DESIGN.md` | Complete | Design system reference |

**What works today:** Upload ISIN-based Excel → FIFO net → bulk BUY entries in Google Finance.

**What's missing:**
- Stock Name / ticker-based Excel input (no ISIN required)
- Per-lot sell recording via Google Finance's native "Record a sale" dialog
- Exchange-aware stock search (NSE vs BSE disambiguation)
- Multi-portfolio batch import support
- Progress persistence across server restarts

---

## Phase 1 — Make It Work for the User's Excel Format (Priority: HIGH)

### Problem
User's Excel looks like this:

```
Stock Name  | Exchange | Quantity | Purchase date | Purchase price
HDFC Bank   | NSE      | 10       | 03/03/2026    | 1055
Reliance    | NSE      | 5        | 15/01/2024    | 2500.50
```

The current parser needs an `isin` column. Stock names are not ISINs.

### Solution: Stock Name Resolver (`backend/stock_resolver.py`)

New resolution pipeline (in priority order):

1. **Exact NSE/BSE symbol match** — `HDFCBANK`, `RELIANCE` etc.
2. **Groww name search** — Same API, but pass stock name + exchange filter
3. **NSE CSV lookup** — Download NSE equity list (static, updated weekly)
4. **Fuzzy match** — Levenshtein distance on company name
5. **Fallback** — Pass the name as-is to Google Finance search (it handles it well)

### Changes to `excel_parser.py`

- Add `stock_name` column alias support: `stock name`, `company`, `name`, `stock`, `script`
- Add `exchange` column alias: `exchange`, `exch`, `market`
- When `isin` column absent but `stock_name` present → resolve via `StockResolver`
- Preserve backward compatibility (ISIN column still works)

### New column format accepted

```
ISIN        | Stock Name | Exchange | Quantity | Buy Date  | Buy Price | Sell Date | Sell Price
(optional)  | HDFC Bank  | NSE      | 10       | 3/3/2026  | 1055      |           |
```

---

## Phase 2 — Native Sell Recording (Priority: MEDIUM)

### The Real Sell Flow (from screenshots)

Google Finance has a proper sell mechanism:
1. Navigate to portfolio page
2. Expand the stock's investment row
3. Click the tag/sell icon next to a purchase lot
4. "Record a sale" dialog appears with:
   - Pre-filled lot details (purchased on X, N shares)
   - Sale date input (date picker)
   - Sale price input
   - "Record all N shares as sold" checkbox
5. Click Save

This is completely different from the current approach (which uses FIFO to net away sells and only enters buy lots).

### Two Modes

| Mode | Use Case | Mechanism |
|------|----------|-----------|
| **FIFO Net Mode** (current) | Bulk historical import — user wants final positions only | Net buys vs sells, upload net buy lots |
| **Full History Mode** (new) | Tax tracking, complete audit trail | Upload every buy, then record every matching sell |

### New Automation Functions (`backend/automation.py`)

```python
async def _record_sale(page, trade: Trade, lot_row_locator) -> None:
    """Click the sell icon on a specific lot and fill in the sale dialog."""

async def _find_investment_lot(page, symbol: str, buy_date: str, quantity: float):
    """Locate a specific purchase lot in the portfolio investments list."""

async def record_sales_for_portfolio(
    trades: list[Trade],
    portfolio_name: str,
    ...
) -> AsyncGenerator[Event, None]:
    """New entry point: navigate to portfolio, find lots, record sales."""
```

### Sell Dialog Selectors (from screenshot analysis)

| Element | Selector | Notes |
|---------|----------|-------|
| Sell icon per lot | `button[aria-label*="sale"]` or tag icon | Need to verify via demo_logger.py |
| "Record a sale" dialog | `.VfPpkd-Sx9Kwc` or `[role="dialog"]` | Standard Material dialog |
| Sale date input | `input[type="date"]` or date picker | Different from buy date picker |
| Sale price input | `input[aria-label*="price"]` or `input[aria-label*="Sale"]` | |
| "Record all shares" checkbox | `input[type="checkbox"]` | |
| Save button | `span.VfPpkd-vQzf8d` filtered by "Save" | Same pattern as buy dialog |

> **Note:** Run `demo_logger.py` on the sell flow before implementing to capture exact selectors.

---

## Phase 3 — Production Hardening (Priority: LOW)

### 3.1 Performance: Parallel ISIN/Name Resolution
- Resolve all symbols concurrently (asyncio.gather) instead of sequentially
- Target: 100 symbols resolved in < 5 seconds

### 3.2 Reliability: Smart Retry + Circuit Breaker
- Per-trade retry already exists (3 attempts)
- Add: detect "Google Finance rate limit" pattern → pause 30s → resume
- Add: checkpoint file — if run interrupted, resume from last successful trade

### 3.3 Scalability: 1000+ Trades
- Current: all trades kept in memory
- Add: streaming processing — process trades in batches of 50
- Add: estimated time remaining in progress events

### 3.4 UX: Better Preview
- Show exchange column in preview table
- Show "will create portfolio / will add to existing" status
- Add "Test ISIN resolution" button before running
- Show estimated runtime

### 3.5 Multi-Portfolio Support
- Allow Excel to have a `portfolio` column
- Group trades by portfolio, create/select each, upload grouped trades

---

## Architecture After All Phases

```
Excel Upload (.xlsx)
      ↓
POST /upload
      ↓
excel_parser.parse_excel()
  ↙ if ISIN column present          ↘ if Stock Name column present
isin_resolver.resolve()          stock_resolver.resolve()
  (Groww API + cache)              (Groww name search + NSE CSV + fuzzy)
      ↓                                    ↓
          Trade objects (symbol resolved)
                    ↓
              fifo.apply_fifo()
                ↙         ↘
         net_trades      summaries
              ↓
POST /run (mode: "fifo_net" | "full_history")
              ↓
GET /progress/{task_id}  →  SSE stream
              ↓
    run_automation()  OR  record_sales_for_portfolio()
              ↓
    Playwright → Google Finance
      ↙ buy flow              ↘ sell flow
  Add investment dialog    Find lot → Record a sale dialog
```

---

## Data Flow for User's Specific Format

```
Input Excel:
  Stock Name | Exchange | Quantity | Purchase date | Purchase price
  HDFC Bank  | NSE      | 10       | 03/03/2026    | 1055

Step 1 — excel_parser.py detects "stock_name" format
  → Creates Trade(isin="", symbol="HDFC Bank", exchange="NSE", ...)

Step 2 — stock_resolver.py resolves "HDFC Bank" + "NSE"
  → Calls Groww search API with name + exchange filter
  → Returns "HDFCBANK" (NSE ticker)
  → Trade.symbol = "HDFCBANK"

Step 3 — fifo.apply_fifo() (no-op if no sells)
  → net_trades = [Trade(symbol="HDFCBANK", qty=10, buy_date=..., buy_price=1055)]

Step 4 — automation.py enters trade in Google Finance
  → Searches "HDFCBANK", selects NSE result
  → Fills 10 shares, 03/03/2026, ₹1055
  → Saves
```

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Groww API name search returns wrong stock | Medium | Wrong investment entered | Preview step shows resolved symbol; user confirms before run |
| Google Finance UI changes sell dialog selectors | High | Sell recording breaks | Run demo_logger.py, update selectors |
| 1000+ trades takes 2+ hours | High | Timeout, orphaned browser | Checkpoint file, resume capability |
| Rate limiting from Google Finance | Medium | Automation blocked | Detect throttle events, add jitter sleeps |
| Stock name ambiguity (multiple listings) | High | Wrong stock selected | Exchange column + priority: NSE > BSE > others |

---

## Success Metrics

- Phase 1: 10 trades from Stock-Name Excel upload successfully in < 3 minutes
- Phase 2: Sell transactions recorded correctly with proper lot matching
- Phase 3: 1000 trades processed reliably with < 2% failure rate, resumable on crash
