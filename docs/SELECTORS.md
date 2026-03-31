# Google Finance DOM Selectors Reference

This document records every verified CSS selector and interaction pattern for the Google Finance portfolio UI.

**Last verified**: 2026-03-31 via `demo_log.jsonl` (live demo recording — 82 events, 3 stocks added).

Whenever Google Finance updates its UI, re-run `demo_logger.py` from the repo root and update this file.

---

## How selectors were verified

```bash
.venv\Scripts\python demo_logger.py
```

The script injects a JS event listener into every page that captures `click`, `input`, and `change` events along with the best available CSS selector for the target element. All events are written to `demo_log.jsonl` in real time.

---

## Page: `/finance/portfolio` (portfolio list)

### Create new portfolio

| Step | Action | Selector / method |
|------|--------|------------------|
| 1 | Click "New portfolio" | `page.locator("span.VfPpkd-vQzf8d", has_text="New portfolio")` |
| 2 | Fill portfolio name | `page.locator("input").first` — ID is dynamic (e.g. `#c40`); always use `.first` |
| 3 | Click Save | `page.locator("span.VfPpkd-vQzf8d", has_text="Save")` |
| 4 | Wait for redirect | `page.wait_for_url(lambda u: "/finance/portfolio/" in u and len(u.split("/")) > 5)` |

Raw log evidence:
```jsonl
{"kind":"click","selector":"span.VfPpkd-vQzf8d","text":"New portfolio"}
{"kind":"input","selector":"input#c40","value":"Testing Portfolio Name"}
{"kind":"click","selector":"span.VfPpkd-vQzf8d","text":"Save"}
{"kind":"navigate","url":"https://www.google.com/finance/portfolio/95021189-a639-47c3-9d0e-6149ea45bdd2"}
```

### List existing portfolios

Portfolio links live in the left sidebar. Reliable filter:
```python
for link in await page.locator("a").all():
    href = await link.get_attribute("href") or ""
    if "/finance/portfolio/" in href and href.count("/") >= 4:
        name = (await link.inner_text()).strip()
```

---

## Page: `/finance/portfolio/<uuid>` (single portfolio)

### Open "Add investment" dialog

**Important**: The button container div changes depending on portfolio state.

| Portfolio state | Container div | Span text |
|-----------------|---------------|-----------|
| Has stocks | `div.a4CLte` | `"add\nInvestment"` |
| Empty (first add) | `div.uFjxEd` | `"add\nAdd investments"` |

```python
# Primary — non-empty portfolio (verified 2026-03-31)
btn = page.locator("div.a4CLte button.VfPpkd-LgbsSe")

# Primary — empty portfolio
btn = page.locator("div.uFjxEd button.VfPpkd-LgbsSe")

# Span-text fallback (has_text is case-insensitive substring match)
btn = page.locator("button span.VfPpkd-vQzf8d", has_text="Investment")
```

**Do NOT use** `button:has(div.VfPpkd-RLmnJb)` as a fallback — it also matches the
`+ New list` tab button, causing it to open a "Create a new list" dialog instead.

Raw log evidence (2026-03-31):
```jsonl
{"kind":"click","selector":"span.VfPpkd-vQzf8d","text":"add\nAdd investments","path":"div.hl8N8b > div > div.oLkttd > div.uFjxEd > ... > button > span"}
{"kind":"click","selector":"span.VfPpkd-vQzf8d","text":"add\nInvestment","path":"div.hl8N8b > div > div.T7rHJe > div.a4CLte > ... > button > span"}
```

---

## Dialog: Add investment

The dialog opens after clicking the FAB. It contains tabs for different asset types.

### 1. Select "Stock" tab

```python
page.locator('div[role="tab"]', has_text="Stock")
```

Raw log evidence:
```jsonl
{"kind":"click","selector":"div[role=\"tab\"]","text":"Stock"}
```

### 2. Search for stock

**Exact aria-label** (verified, reliable):
```python
page.locator('input[aria-label="Type an investment name or symbol"]')
```

Fill with NSE ticker symbol (e.g. `"RELIANCE"`) or ISIN. Wait ~1.5 s for suggestions to load.

Raw log evidence:
```jsonl
{"kind":"click","selector":"input[aria-label=\"Type an investment name or symbol\"]"}
{"kind":"input","selector":"input[aria-label=\"Type an investment name or symbol\"]","value":"Reliance"}
```

### 3. Select first suggestion

Suggestion rows observed (2026-03-31):
- **`div.onRPD`** — outer clickable row (always present, recommended)
- **`div.CrPloe`** — inner content div, child of `div.onRPD`

Both contain the full text: `"Infosys Ltd\nINFY : NSE (IN)\n₹1,247.80\n1.72%"`

```python
# Primary — outer row, prefer NSE in text
rows = page.locator("div.onRPD")
for item in await rows.all():
    if "NSE" in (await item.inner_text()).upper():
        await item.click()
        break

# Fallback — inner content div
rows = page.locator("div.CrPloe")
```

Raw log evidence (2026-03-31):
```jsonl
{"kind":"click","selector":"div.onRPD","text":"Infosys Ltd\nINFY : NSE (IN)\n₹1,247.80\n1.72%"}
{"kind":"click","selector":"div.CrPloe","text":"Reliance Industries Ltd\nRELIANCE : NSE (IN)\n₹1,348.30\n0.015%"}
```

### 4. Fill Quantity

Input ID is dynamic (`#c122`, etc.). Use `aria-label` instead:

```python
qty = page.get_by_label("Quantity")
# Fallback
qty = page.get_by_label("Shares")
```

Use `click(click_count=3)` then `fill()` to clear any pre-filled value.
Note: `triple_click()` does not exist on Playwright's Locator — use `click(click_count=3)`.

Raw log evidence:
```jsonl
{"kind":"input","selector":"input#c122","value":"100"}
```

### 5. Set Purchase date

**Important**: This is a **calendar picker**, not a `<input type="date">`.

```python
# 1. Click the date input to open the calendar
date_input = page.locator("input.whsOnd.zHQkBf")
await date_input.click()

# 2. Navigate months using labelled buttons
prev = page.get_by_role("button", name="Previous month")
next = page.get_by_role("button", name="Next month")

# 3. Read current month from the calendar header
header = page.locator('[role="dialog"] [aria-live="polite"]')
# Header text format: "March 2026"
shown = datetime.strptime(header_text, "%B %Y")

# 4. Click the target day
day_cell = page.locator('div[role="gridcell"]', has_text=str(target_day))
# Filter for exact text match to avoid day 8 matching day 18
for cell in await day_cell.all():
    if (await cell.inner_text()).strip() == str(target_day):
        await cell.click()
        break
```

Raw log evidence:
```jsonl
{"kind":"click","selector":"input.whsOnd.zHQkBf","text":""}
{"kind":"click","selector":"div[role=\"gridcell\"]","text":"8"}
```

### 6. Fill Purchase price

Input ID is dynamic (`#c125`). Pre-filled with current market price — must triple-click to clear.

```python
price_input = page.get_by_label("Purchase price")
# Fallbacks
price_input = page.get_by_label("Price per share")
price_input = page.get_by_label("Price")
price_input = page.locator("input[aria-label*='rice']").first
```

Raw log evidence:
```jsonl
{"kind":"click","selector":"input#c125","text":"1404.80"}
```

### 7. Save the transaction

```python
# Primary — text-matched span inside Material button
save = page.locator("span.VfPpkd-vQzf8d", has_text="Save")
# Fallback
save = page.get_by_role("button", name="Save")
```

Raw log evidence:
```jsonl
{"kind":"click","selector":"div.VfPpkd-RLmnJb","text":""}
{"kind":"navigate","url":"https://www.google.com/finance/portfolio/95021189-a639-47c3-9d0e-6149ea45bdd2"}
```

---

## Auth check

```python
# Logged in when this element is present
page.wait_for_selector('a[aria-label*="Google Account"]', timeout=7_000)
```

---

## Material Design button pattern

Google Finance uses Material Design (MDC Web). Button labels are inside `span.VfPpkd-vQzf8d`. The ripple layer is `div.VfPpkd-RLmnJb`. When matching buttons by text, prefer:

```python
# Most reliable — targets the label span
page.locator("span.VfPpkd-vQzf8d", has_text="Button Label")

# Standard Playwright role (works if aria-label or visible text matches)
page.get_by_role("button", name="Button Label")
```

---

## Maintenance

When this table goes stale:

1. `python demo_logger.py` — record a new session
2. Look at `demo_log.jsonl` for lines with `"kind": "click"` or `"kind": "input"`
3. Update the affected rows in this file
4. Update the corresponding code in `backend/automation.py`
5. Run dry-run test to confirm no regressions in parsing
