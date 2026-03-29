# Google Finance Bulk Upload

A FastAPI + Playwright tool that reads buy/sell trades from an Excel spreadsheet and automatically enters them into a [Google Finance](https://www.google.com/finance/portfolio) portfolio basket.

## Features

- Drag-and-drop Excel upload via a dark-themed web UI
- ISIN → NSE/BSE ticker resolution via Groww public API (cached locally)
- Playwright-driven browser automation with saved login state (login once, reuse forever)
- Server-Sent Events (SSE) progress stream — watch every step in real time
- Dry-run mode — validates and resolves symbols without opening a browser
- Per-trade retry logic (3 attempts) with automatic portfolio re-navigation on failure

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Google account with access to Google Finance

### 2. Install dependencies

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

### 3. Start the server

```bash
# Windows
.venv\Scripts\python -m uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000` in your browser.

### 4. First run — manual login

On the first automation run the browser opens visibly. Log in to your Google account manually. Playwright saves the full browser state to `auth/gf_state.json`. All subsequent runs load that state and skip the login screen entirely.

To force a re-login, click the **Auth saved** badge in the top-right of the UI, or call `DELETE /auth`.

## Excel File Format

| Column | Required | Accepted names | Format |
|--------|----------|---------------|--------|
| ISIN | Yes | `isin` | `INE002A01018` |
| Quantity | Yes | `quantity`, `qty`, `shares` | `10` |
| Buy Date | Yes | `buy date`, `buy_date`, `purchase date` | `DD-MM-YYYY` or `DD/MM/YYYY` |
| Buy Price | Yes | `buy price`, `buy_price`, `purchase price`, `cost price` | `2500.50` |
| Sell Date | No | `sell date`, `sell_date` | same as buy date |
| Sell Price | No | `sell price`, `sell_price` | `3100.00` |

- Column names are **case-insensitive** and **order-independent**.
- Leave sell date/price blank for buy-only rows.
- The `skip_rows` field in the UI lets you skip extra header rows above the column names.

A sample file is included: [`sample_trade.xlsx`](sample_trade.xlsx)

## Project Structure

```
google-finance-auto/
├── backend/
│   ├── main.py            # FastAPI app — all HTTP routes
│   ├── automation.py      # Playwright automation engine
│   ├── excel_parser.py    # Excel → Trade dataclass parser
│   ├── fifo.py            # FIFO accounting engine
│   └── isin_resolver.py   # ISIN → NSE ticker via Groww API
├── frontend/
│   └── index.html         # Single-file UI (no build step)
├── docs/
│   ├── SELECTORS.md       # Verified DOM selectors reference
│   └── DESIGN.md          # UI design tokens and component guide
├── data/
│   └── isin_cache.json    # Persistent ISIN resolution cache
├── auth/
│   └── gf_state.json      # Playwright browser state (created on first login)
├── uploads/               # Temp directory for uploaded files (auto-cleaned)
├── demo_logger.py         # Interactive selector recorder (dev tool)
├── sample_trade.xlsx      # One-row example spreadsheet
├── tradebook-VZN823.xlsx  # Full trade history example
├── requirements.txt
└── .env.example
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves `frontend/index.html` |
| `GET` | `/status` | Returns `{"auth_saved": bool, "auth_path": str}` |
| `DELETE` | `/auth` | Clears `auth/gf_state.json` — forces re-login |
| `POST` | `/upload` | Upload `.xlsx`, returns `task_id` + parsed trades preview |
| `POST` | `/run` | Attach run config to a `task_id` |
| `GET` | `/progress/{task_id}` | SSE stream of `Event` objects |

### SSE Event shape

```json
{"kind": "info|success|warning|error|done", "message": "...", "row": 1, "detail": ""}
```

## Developer Tools

### Selector recorder

```bash
.venv\Scripts\python demo_logger.py
```

Opens a browser, logs every click/input/navigation to `demo_log.jsonl`. Use this whenever Google Finance updates its UI — record a manual session, then update selectors in `backend/automation.py` and [`docs/SELECTORS.md`](docs/SELECTORS.md).

### Dry-run test (no browser)

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

## ISIN Cache

Resolved ISINs are stored in `data/isin_cache.json`. If Groww's API can't resolve an ISIN, the raw ISIN is passed to Google Finance (which also accepts ISINs in its search). You can manually pre-populate the cache:

```json
{
  "INE002A01018": "RELIANCE",
  "INE009A01021": "INFY"
}
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Browser opens but doesn't log in | Auth state is stale — click auth badge in UI to clear, then re-run |
| "Could not find Add investment button" | Google Finance UI updated — run `demo_logger.py`, record the flow, update selectors in `automation.py` |
| Date not filling correctly | Calendar month mismatch — check `_select_calendar_date()` in `automation.py`; the header parser expects `"Month YYYY"` format |
| ISIN resolves to wrong ticker | Edit `data/isin_cache.json` directly to override |
| Port 8000 already in use | `uvicorn backend.main:app --port 8001` |
