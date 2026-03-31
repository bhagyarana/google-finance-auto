# TODO.md — Google Finance Bulk Trade Automator

> Status key: `[ ]` = not started · `[~]` = in progress · `[x]` = done · `[!]` = blocked

---

## PHASE 1 — Stock Name Support (User's Excel Format)

### 1.1 New: `backend/stock_resolver.py`

- [ ] **1.1.1** Create `StockResolver` class with in-memory + disk cache (mirrors `isin_resolver.py` pattern)
- [ ] **1.1.2** Implement `resolve_by_name(name: str, exchange: str = "NSE") -> str`
  - Normalize input: strip extra spaces, title-case company name
  - Try Groww name search: `GET /search/v3/query/global/st_p_query?query={name}`
  - Filter results by exchange label (`NSE (IN)` or `BSE (IN)`)
  - Return `nse_scrip_code` or `bse_scrip_code` from first matching result
- [ ] **1.1.3** Implement `resolve_batch(items: list[dict]) -> dict` — concurrent resolution with `asyncio.gather`
- [ ] **1.1.4** Add NSE static CSV fallback
  - Download NSE equity master: `https://archives.nseindia.com/content/equities/EQUITY_L.csv`
  - Cache locally in `data/nse_equity.csv` (refresh if > 7 days old)
  - Build lookup dict: company name → symbol
- [ ] **1.1.5** Add fuzzy match fallback (use `difflib.get_close_matches`, no extra dependencies)
- [ ] **1.1.6** Persist resolved cache to `data/stock_name_cache.json`
- [ ] **1.1.7** Write 5 test assertions (HDFC Bank→HDFCBANK, Reliance→RELIANCE, Infosys→INFY, TCS→TCS, WIPRO→WIPRO)

---

### 1.2 Update: `backend/excel_parser.py`

- [ ] **1.2.1** Add `stock_name` aliases to `_COL_ALIASES`:
  ```python
  "stock_name": ["stock name", "company", "name", "stock", "script", "scrip", "security"]
  "exchange": ["exchange", "exch", "market", "listing"]
  ```
- [ ] **1.2.2** Add `exchange` field to `Trade` dataclass (default `"NSE"`)
- [ ] **1.2.3** Update `_detect_format()` to recognize "stock_name format":
  - If `stock_name` column present AND `isin` column absent → `"stock_name"` format
  - If both present → use ISIN (existing behavior)
- [ ] **1.2.4** In `_parse_row()` for stock_name format:
  - Read `stock_name`, `exchange` columns
  - Store in `trade.symbol` (raw name) and `trade.exchange`
  - Set `trade.isin = ""` (empty — will be resolved later)
- [ ] **1.2.5** Ensure column validation gives helpful error when neither ISIN nor Stock Name present
- [ ] **1.2.6** Update `parse_excel()` return shape to include `"format"` key in metadata

---

### 1.3 Update: `backend/main.py`

- [ ] **1.3.1** In `/upload` handler: after `parse_excel()`, check if `trade.isin == ""` (stock_name format)
- [ ] **1.3.2** Call `stock_resolver.resolve_batch()` for stock_name trades
- [ ] **1.3.3** Return `resolved_symbols` map in upload response for preview
- [ ] **1.3.4** Add `GET /resolve-preview` endpoint — accepts stock name + exchange, returns resolved symbol (for inline preview testing)

---

### 1.4 Update: `frontend/index.html`

- [ ] **1.4.1** In raw trades preview table: add `Exchange` column (show if present)
- [ ] **1.4.2** Add "resolved as" badge next to symbol in preview (e.g., `HDFC Bank → HDFCBANK`)
- [ ] **1.4.3** Show warning toast if any symbol failed to resolve (fallback to name)
- [ ] **1.4.4** Add inline "test resolve" icon per row in preview table
- [ ] **1.4.5** Update column legend / help tooltip to list both ISIN and Stock Name formats

---

### 1.5 Update: `backend/automation.py`

- [ ] **1.5.1** In `_search_and_select_stock()`: when exchange is provided, prioritize that exchange in the suggestions dropdown
  ```python
  # Current: picks first result
  # New: picks result where exchange label matches trade.exchange
  exchange_label = "NSE (IN)" if trade.exchange == "NSE" else "BSE (IN)"
  # Filter suggestions by exchange_label span text
  ```
- [ ] **1.5.2** Pass `trade.exchange` through the automation call chain
- [ ] **1.5.3** Log which exchange was matched (INFO event) for user visibility

---

### 1.6 Sample Data & Docs

- [ ] **1.6.1** Create `sample_stock_name.xlsx` — 5 trades in stock-name format:
  ```
  Stock Name | Exchange | Quantity | Purchase date | Purchase price
  HDFC Bank  | NSE      | 10       | 03/03/2026    | 1055
  Reliance   | NSE      | 5        | 15/01/2024    | 2500.50
  Infosys    | NSE      | 20       | 10/06/2023    | 1350
  TCS        | NSE      | 3        | 22/09/2023    | 3200
  Wipro      | NSE      | 15       | 05/12/2023    | 430
  ```
- [ ] **1.6.2** Update `CLAUDE.md` — add stock_name format to "Excel column names accepted" section
- [ ] **1.6.3** Update `docs/SELECTORS.md` — add exchange-aware stock search logic

---

## PHASE 2 — Native Sell Recording

### 2.1 Capture Sell Dialog Selectors

- [ ] **2.1.1** Run `demo_logger.py`, navigate to a portfolio with a buy entry
- [ ] **2.1.2** Click the sell icon next to a purchase lot — record what element it is
- [ ] **2.1.3** Fill "Record a sale" dialog: date input, price input, checkbox, Save
- [ ] **2.1.4** Document all selectors in `docs/SELECTORS.md` under new section "Record a Sale Dialog"
- [ ] **2.1.5** Identify: is the date input a standard `<input type="date">` or the same calendar widget?

---

### 2.2 New data model additions

- [ ] **2.2.1** Add `SellRecord` dataclass to `excel_parser.py`:
  ```python
  @dataclass
  class SellRecord:
      row: int
      symbol: str          # NSE ticker
      isin: str
      exchange: str
      quantity: float
      sell_date: str       # MM/DD/YY
      sell_price: float
      buy_date: str        # to match against existing lot
  ```
- [ ] **2.2.2** Update `parse_excel()` to emit `SellRecord` objects when `trade_type == "sell"` in full-history mode
- [ ] **2.2.3** Add `mode` parameter to `parse_excel()`: `"fifo_net"` (default, existing) | `"full_history"` (new)

---

### 2.3 New automation functions in `backend/automation.py`

- [ ] **2.3.1** Implement `_find_investment_lots(page, symbol: str) -> list[Locator]`
  - Navigate to portfolio, expand investment row for `symbol`
  - Return list of locators for each purchase lot row
- [ ] **2.3.2** Implement `_click_sell_icon(page, lot_locator) -> None`
  - Find and click the sell/tag icon in the lot row
  - Wait for "Record a sale" dialog to appear
- [ ] **2.3.3** Implement `_fill_sell_dialog(page, sell_record: SellRecord) -> None`
  - Fill sale date (detect input type — date picker or text)
  - Fill sale price
  - Handle "Record all N shares as sold" checkbox (check or uncheck)
  - Click Save
- [ ] **2.3.4** Implement `_record_sale(page, sell_record: SellRecord) -> None`
  - Orchestrate: find lot → click sell icon → fill dialog → save
  - With 3-retry wrapper
- [ ] **2.3.5** Add new entry point `run_sell_recording()` async generator
  - Parameters: `sell_records: list[SellRecord]`, `portfolio_name: str`, ...
  - Navigate to portfolio
  - Loop sell records, call `_record_sale()` per record
  - Emit SSE events matching existing event schema

---

### 2.4 New API endpoint in `backend/main.py`

- [ ] **2.4.1** Add `POST /run-sells` endpoint
  - Accepts `task_id` (where sell records are stored) + config
  - Launches `run_sell_recording()` generator
- [ ] **2.4.2** Store `sell_records` in `_tasks[task_id]` alongside `net_trades`
- [ ] **2.4.3** Add `GET /progress-sells/{task_id}` SSE endpoint (or reuse `/progress` with type flag)

---

### 2.5 Frontend: Sell Recording Mode

- [ ] **2.5.1** Add "Import Mode" toggle in Step 3 (Configure):
  - `FIFO Net` — upload net buy positions only (existing behavior)
  - `Full History` — upload all buys + record all sells separately
- [ ] **2.5.2** Show "Sell Transactions" section in Step 2 preview when full-history mode selected
- [ ] **2.5.3** Show sell records table: Symbol | Exchange | Qty | Sell Date | Sell Price | Matched Lot
- [ ] **2.5.4** After buy automation completes, prompt "Now recording sell transactions..." and stream sell progress
- [ ] **2.5.5** Show combined progress (buys + sells) in a single progress bar

---

### 2.6 Lot Matching Logic (`backend/fifo.py` extension)

- [ ] **2.6.1** Add `match_sells_to_lots(buys: list[Trade], sells: list[SellRecord]) -> list[MatchedSell]`
  - For each sell, find the FIFO-matched buy lot
  - Return `MatchedSell(sell_record, matched_buy_date, matched_buy_qty)`
- [ ] **2.6.2** The matched buy_date is what we use to find the lot in Google Finance UI
- [ ] **2.6.3** Handle partial lot sells (200 shares from a 300-share lot)

---

## PHASE 3 — Production Hardening

### 3.1 Performance

- [ ] **3.1.1** Parallelize symbol resolution in Phase 1 (`asyncio.gather` with semaphore limit 5)
- [ ] **3.1.2** Add progress events for resolution step: "Resolving 50/1000 symbols..."
- [ ] **3.1.3** Cache NSE CSV in memory once per server session (not per-upload)
- [ ] **3.1.4** Add ETA calculation: `trades_per_minute * remaining_trades`

---

### 3.2 Reliability: Checkpoint & Resume

- [ ] **3.2.1** Add `checkpoint.json` written after each successful trade: `{task_id, completed_rows: [1,3,5,...], timestamp}`
- [ ] **3.2.2** On new run with same task_id: skip rows already in `completed_rows`
- [ ] **3.2.3** Add `POST /resume/{task_id}` endpoint that uses checkpoint
- [ ] **3.2.4** Frontend: detect interrupted task (from localStorage), offer "Resume" button on load
- [ ] **3.2.5** Clear checkpoint on full completion or user reset

---

### 3.3 Rate Limiting Defense

- [ ] **3.3.1** Detect "Too many requests" or captcha-like states in the browser
- [ ] **3.3.2** On detection: emit `warning` event, pause 60s, retry navigation
- [ ] **3.3.3** Add configurable delay between trades (default 1.0s, range 0.5–5.0s) in frontend config
- [ ] **3.3.4** Add jitter: `sleep(base_delay + random(0, 0.5))`

---

### 3.4 Multi-Portfolio Support

- [ ] **3.4.1** Add optional `portfolio` column in Excel (alias: `portfolio`, `basket`, `account`)
- [ ] **3.4.2** Group trades by portfolio name in `excel_parser.py`
- [ ] **3.4.3** Run automation per portfolio group sequentially
- [ ] **3.4.4** Frontend: show grouped preview (collapsible by portfolio)

---

### 3.5 UX Improvements

- [ ] **3.5.1** Add "Copy template" button in UI — downloads a blank Excel in user's format (ISIN or Stock Name)
- [ ] **3.5.2** Show live "X of Y trades done, ~N minutes remaining" in progress view
- [ ] **3.5.3** Add "Pause" and "Stop" buttons during automation (needs SSE command back-channel)
- [ ] **3.5.4** Export automation log as `.txt` after run completes
- [ ] **3.5.5** Show per-trade status summary table after run: Symbol | Status | Details
- [ ] **3.5.6** Add `skip_rows` auto-detection (scan first 10 rows, find header row automatically)

---

### 3.6 Dev Tooling

- [ ] **3.6.1** Add `--validate-only` flag to dry-run: parses + resolves symbols, no browser, prints full report
- [ ] **3.6.2** Add `GET /debug/task/{task_id}` endpoint (dev-only) to inspect full task state
- [ ] **3.6.3** Add recordings auto-cleanup: delete recordings older than 7 days on server start
- [ ] **3.6.4** Add `GET /health` endpoint returning server version, auth state, task count

---

## IMMEDIATE NEXT ACTIONS (Start Here)

Priority order for first implementation session:

```
1. [ ] 1.1 — Create stock_resolver.py (Groww name search + NSE CSV)
2. [ ] 1.2 — Update excel_parser.py (stock_name + exchange columns)
3. [ ] 1.3 — Update main.py (call stock_resolver in /upload)
4. [ ] 1.4 — Update frontend (show exchange, resolved symbol badge)
5. [ ] 1.5 — Update automation.py (exchange-aware stock selection)
6. [ ] 1.6 — Create sample_stock_name.xlsx + update docs
```

Then validate with:
```bash
.venv\Scripts\python -c "
import sys, asyncio, os
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'backend')
from excel_parser import parse_excel
from automation import run_automation

async def test():
    trades, _ = parse_excel('sample_stock_name.xlsx')
    async for e in run_automation(trades=trades, portfolio_name='Test', dry_run=True):
        print(f'[{e.kind.upper()}] {e.message}')

asyncio.run(test())
"
```

Expected output:
```
[INFO] Resolving 5 stock symbol(s)...
[INFO]   HDFC Bank (NSE) -> HDFCBANK
[INFO]   Reliance (NSE) -> RELIANCE
[INFO]   Infosys (NSE) -> INFY
[INFO]   TCS (NSE) -> TCS
[INFO]   Wipro (NSE) -> WIPRO
[INFO] Dry-run mode - skipping browser automation.
[SUCCESS] [dry-run] HDFCBANK, 10.0 shares, buy 03/03/26 @ 1055.0
...
[DONE] Dry run complete.
```

---

## Open Questions (Need User Input)

1. **Sell recording approach**: Do you want FIFO net mode (current, simpler) or Full History mode (records every buy + every sell as separate transactions in GF)?

2. **Exchange preference**: When a stock is listed on both NSE and BSE, always prefer NSE? Or let user specify per-row?

3. **Portfolio naming**: Should all trades go to one named portfolio, or do you want a `portfolio` column per Excel row?

4. **Rate limiting**: Are you okay with 1-2 second delays between entries? Or do you need maximum speed?

5. **Error handling**: If one trade fails (stock not found), should we skip and continue, or stop the entire run?

---

## Dependency Map

```
Phase 1.1 (stock_resolver)  ──→  Phase 1.2 (parser update)
Phase 1.2 (parser update)   ──→  Phase 1.3 (main.py)
Phase 1.3 (main.py)         ──→  Phase 1.4 (frontend)
Phase 1.5 (automation)       ─── (independent, can be done in parallel with 1.2-1.4)

Phase 2.1 (capture selectors) ──→  Phase 2.3 (automation functions)
Phase 2.2 (data model)        ──→  Phase 2.3, Phase 2.4
Phase 2.3 + 2.4 (backend)    ──→  Phase 2.5 (frontend)
Phase 2.6 (lot matching)      ──→  Phase 2.3 (uses matched lots)

Phase 3.* — all independent, add in any order
```
