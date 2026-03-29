# CLAUDE.md — Agent Instructions for google-finance-auto

This file gives Claude Code (and any AI agent) the context needed to work on this project without requiring repeated explanation.

## What this project does

Reads buy/sell stock trades from an Excel file and automates entering them into a Google Finance portfolio basket using Playwright. A FastAPI backend serves a single-page frontend; the frontend uploads the Excel, previews parsed trades, then streams real-time progress via SSE while Playwright automates the browser.

## How to run

```bash
# Start the server (from repo root)
.venv\Scripts\python -m uvicorn backend.main:app --reload   # Windows
.venv/bin/python -m uvicorn backend.main:app --reload        # macOS/Linux
```

Open `http://127.0.0.1:8000`.

## Key files and what they do

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI routes: `/upload`, `/run`, `/progress/{id}` (SSE), `/status`, `/auth` |
| `backend/automation.py` | Playwright automation engine — all Google Finance interaction logic |
| `backend/excel_parser.py` | Parses `.xlsx` → list of `Trade` dataclasses (buy and sell-only rows) |
| `backend/fifo.py` | FIFO accounting engine — nets sells against buys, returns surviving lots |
| `backend/isin_resolver.py` | ISIN → NSE ticker via Groww API + `data/isin_cache.json` |
| `frontend/index.html` | Single-file UI — no build step, no dependencies |
| `data/isin_cache.json` | Persistent ISIN resolution cache (edit to override bad lookups) |
| `auth/gf_state.json` | Playwright browser storage state — created after first manual login |
| `demo_logger.py` | Dev tool: records all browser interactions to `demo_log.jsonl` (generated, not committed) |
| `sample_trade.xlsx` | One-row test file: INE002A01018 (RELIANCE), 10 shares, 15-01-2024, ₹2500.50 |
| `tradebook-VZN823.xlsx` | Full trade history example with multiple ISINs |

## Architecture

```
Excel upload
    ↓
POST /upload  →  excel_parser.parse_excel()  →  list[Trade] (raw: buys + sells)
                           ↓
                  fifo.apply_fifo()  →  FifoResult
                    ├── net_trades  →  stored in _tasks[task_id]   (only these go to GF)
                    └── summaries   →  returned in upload JSON for UI preview
    ↓
POST /run     →  attaches portfolio_name, dry_run, headless flags to task
    ↓
GET /progress/{task_id}  →  SSE  →  run_automation() async generator
    ↓
    ├── isin_resolver.resolve() per trade
    ├── Playwright: login (or load auth state)
    ├── Open / create portfolio
    └── Per trade: Add investment dialog → fill form → save
```

## Critical automation selectors

These were verified by live demo recording (`demo_log.jsonl`). **Do not guess selectors — consult this table or re-run `demo_logger.py` to verify.**

| UI element | Verified selector |
|-----------|------------------|
| "New portfolio" button | `span.VfPpkd-vQzf8d` filtered by text `"New portfolio"` |
| Portfolio name input | first `<input>` in the dialog (ID is dynamic, e.g. `#c40`) |
| "Save" button (dialog) | `span.VfPpkd-vQzf8d` filtered by text `"Save"` |
| Add investment FAB | `button:has(div.VfPpkd-RLmnJb)` → fallback: `div.VfPpkd-RLmnJb` directly |
| "Stock" tab in dialog | `div[role="tab"]` filtered by text `"Stock"` |
| Stock search input | `input[aria-label="Type an investment name or symbol"]` ← exact |
| First suggestion row | `[role='listbox'] [role='option']` first, then `ul[role='listbox'] li` first |
| Quantity input | `get_by_label("Quantity")` → `get_by_label("Shares")` |
| Date input (opens calendar) | `input.whsOnd.zHQkBf` |
| Calendar day cell | `div[role="gridcell"]` inner text == target day number |
| Calendar prev/next | `button[aria-label="Previous month"]` / `button[aria-label="Next month"]` |
| Purchase price input | `get_by_label("Purchase price")` → `get_by_label("Price per share")` |
| Save transaction button | `span.VfPpkd-vQzf8d` filtered by `"Save"` / `"Done"` / `"Add"` |

Full selector documentation: [`docs/SELECTORS.md`](docs/SELECTORS.md)

## FIFO accounting (`backend/fifo.py`)

Google Finance has no sell-transaction entry. All sells are resolved via FIFO before automation runs.

**Row types the parser accepts:**

| Row | `trade_type` | `buy_date/price` | `sell_date/price` | Meaning |
|-----|-------------|------------------|-------------------|---------|
| Buy-only | `"buy"` | set | None | Pure purchase lot |
| Sell-only | `"sell"` | None | set | Pure sell — `quantity` = shares sold |
| Combined | `"buy"` | set | set | Same qty bought and sold (legacy format) |

**FIFO algorithm:**
1. Group all rows by ISIN.
2. Sort buy lots oldest-first (FIFO queue).
3. For each sell (oldest first), consume from front of the buy queue.
4. Partial lots supported: a 10-share buy lot half-sold leaves 5 shares at the original price.
5. Remaining buy lots → `Trade` objects passed to automation.
6. `FifoSummary` per ISIN returned in `/upload` JSON: `total_bought`, `total_sold`, `net_quantity`, `realized_gain`.

**Example:**
```
BUY  10 @ ₹100  → lot A
SELL  5 @ ₹120  → consumes 5 of lot A
SELL  2 @ ₹80   → consumes 2 of lot A
                 → 3 shares of lot A survive
Realized P&L = (120-100)*5 + (80-100)*2 = ₹60
Net position added to GF: 3 shares @ ₹100
```

## Trade dataclass

```python
@dataclass
class Trade:
    row: int                   # 1-based spreadsheet row
    isin: str                  # raw ISIN (e.g. "INE002A01018")
    symbol: str                # resolved NSE ticker (e.g. "RELIANCE") — filled by automation
    quantity: float
    buy_date: Optional[str]    # MM/DD/YY — None for sell-only rows
    buy_price: Optional[float] # None for sell-only rows
    sell_date: Optional[str]   # MM/DD/YY or None
    sell_price: Optional[float]
    has_sell: bool
    trade_type: str            # "buy" | "sell"
```

## SSE Event shape

```python
Event(kind, message, row=None, detail="")
# kind: "info" | "success" | "warning" | "error" | "done"
```

The frontend listens to `/progress/{task_id}` and displays events in a live log panel. Progress bar advances on each `"success"` event with a `row` value.

## Excel column names accepted

Case-insensitive, order-independent. Aliases defined in `excel_parser._COL_ALIASES`:

- **isin**: `isin`
- **quantity**: `quantity`, `qty`, `shares`
- **buy_date**: `buy date`, `buy_date`, `purchase date`
- **buy_price**: `buy price`, `buy_price`, `purchase price`, `cost price`
- **sell_date**: `sell date`, `sell_date` (optional)
- **sell_price**: `sell price`, `sell_price` (optional)

## ISIN resolver behaviour

1. Check in-memory `_cache` dict
2. Check `data/isin_cache.json`
3. Call Groww public API: `https://groww.in/v1/api/search/v3/query/global/st_p_query?query={isin}`
4. Fall back to returning the raw ISIN (Google Finance search also accepts ISINs)

Results from step 3 are saved to both cache layers. To override a bad lookup, edit `data/isin_cache.json` directly.

## Auth state

- Location: `auth/gf_state.json`
- Created: after first manual browser login during an automation run, or implicitly by `demo_logger.py`
- Cleared: `DELETE /auth` endpoint, or delete the file manually
- Re-login: triggered automatically when the file is missing on next run

## When Google Finance changes its UI

1. Run `demo_logger.py` — a browser opens pointing to `/finance/portfolio`
2. Perform the full "add investment" flow manually
3. Close the browser — `demo_log.jsonl` is written
4. Update selectors in `backend/automation.py` (see `docs/SELECTORS.md` for the full reference)
5. Re-run dry-run test to confirm parsing still works

## Running tests

No test suite currently. Use the dry-run path to validate end-to-end parsing and symbol resolution without a browser:

```bash
.venv\Scripts\python -c "
import sys, asyncio, os
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'backend')
from excel_parser import parse_excel
from automation import run_automation

async def test():
    trades, _ = parse_excel('sample_trade.xlsx')
    async for e in run_automation(trades=trades, portfolio_name='Test', dry_run=True):
        print(f'[{e.kind.upper()}] {e.message}')

asyncio.run(test())
"
```

Expected output:
```
[INFO] Resolving 1 ISIN symbol(s)…
[INFO]   INE002A01018 → RELIANCE
[INFO] Dry-run mode — skipping browser automation.
[SUCCESS] [dry-run] INE002A01018 → RELIANCE, 10.0 shares, buy 01/15/24 @ 2500.5
[DONE] Dry run complete.
```

## Known limitations

- **Date picker**: Google Finance uses a calendar grid (not `<input type="date">`). The `_select_calendar_date()` helper navigates month-by-month. If the calendar header format changes from `"Month YYYY"`, the navigation will silently skip and land on the wrong month.
- **Dynamic input IDs**: Fields like Quantity and Price have unpredictable DOM IDs (`#c122`, `#c125`). The code uses `aria-label` instead; if Google removes those labels, use `demo_logger.py` to find the new selectors.
- **Sell transactions**: The Sell flow reuses the same Add investment dialog. If Google Finance adds a separate sell entry point, update `_process_trade()`.
- **Windows console encoding**: Print statements with Unicode arrows (`→`, `—`) fail on Windows unless `PYTHONIOENCODING=utf-8` is set. The server itself (uvicorn) is unaffected.
