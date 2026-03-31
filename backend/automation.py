"""Google Finance portfolio automation using Playwright. See docs/SELECTORS.md for verified selectors."""

from __future__ import annotations

import asyncio
import json
import os
import queue as _queue
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
)

from excel_parser import Trade
from isin_resolver import resolve

BASE_DIR      = Path(__file__).parent.parent
AUTH_DIR      = BASE_DIR / "auth"
AUTH_STATE    = AUTH_DIR / "gf_state.json"

GF_HOME      = "https://www.google.com/finance"
GF_PORTFOLIO = "https://www.google.com/finance/portfolio"

# ---------------------------------------------------------------------------
# Progress event type
# ---------------------------------------------------------------------------

class Event:
    """Simple progress event emitted during automation."""
    def __init__(self, kind: str, message: str, row: Optional[int] = None, detail: str = ""):
        self.kind    = kind      # "info" | "success" | "warning" | "error" | "done"
        self.message = message
        self.row     = row
        self.detail  = detail

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "row": self.row, "detail": self.detail}


# ---------------------------------------------------------------------------
# Browser / context management
# ---------------------------------------------------------------------------

async def _launch_context(
    pw: Playwright,
    headless: bool,
) -> tuple[Browser, BrowserContext]:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)

    browser = await pw.chromium.launch(
        headless=headless,
        channel="chrome",  # Use real Chrome — Google blocks Playwright's bundled Chromium
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
        slow_mo=80,
    )

    storage = str(AUTH_STATE) if AUTH_STATE.exists() else None
    context = await browser.new_context(
        storage_state=storage,
        viewport={"width": 1400, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    # Spoof navigator.webdriver so Google login doesn't detect automation
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def _is_logged_in(page: Page) -> bool:
    try:
        await page.goto(GF_HOME, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_selector('a[aria-label*="Google Account"]', timeout=7_000)
        return True
    except PWTimeout:
        return False


async def _manual_login(page: Page) -> None:
    await page.goto(GF_HOME, wait_until="domcontentloaded")
    print(
        "\n[automation] *** MANUAL LOGIN REQUIRED ***\n"
        "  A browser window has opened. Please log in to your Google account.\n"
        "  The script will continue automatically once you're logged in.\n"
    )
    await page.wait_for_selector('a[aria-label*="Google Account"]', timeout=180_000)


# ---------------------------------------------------------------------------
# Portfolio helpers
# ---------------------------------------------------------------------------

async def _switch_to_portfolios_tab(page: Page) -> None:
    """
    Click the 'Portfolios' section tab if present.

    New UI (2026-03-30): google.com/finance/portfolio now shows 'Watchlists'
    by default.  The 'Portfolios' tab is a span.AICEr element in the page
    header (confirmed via demo_logger.py on 2026-03-30).
    Clicking it reveals portfolio-specific tabs and the '+ New portfolio' button.
    """
    for sel in [
        'span.AICEr:has-text("Portfolios")',   # confirmed 2026-03-30
        'a:has-text("Portfolios")',
        '[role="tab"]:has-text("Portfolios")',
        'span:has-text("Portfolios")',
    ]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            await asyncio.sleep(1.0)
            return


async def _get_portfolio_map(page: Page) -> dict[str, str]:
    """Return {portfolio_name: url} for all portfolios.

    New UI (2026-03-30): google.com/finance/portfolio shows Watchlists by
    default.  Must click the 'Portfolios' tab first to see portfolio links.
    Hrefs changed from /finance/portfolio/<uuid> to ./portfolio/<uuid>.
    """
    await page.goto(GF_PORTFOLIO, wait_until="domcontentloaded", timeout=20_000)
    await asyncio.sleep(1)  # brief settle — networkidle never fires on GF
    await _switch_to_portfolios_tab(page)

    current_origin = "https://www.google.com"

    result: dict[str, str] = {}
    for link in await page.locator("a").all():
        href = await link.get_attribute("href") or ""
        # Match both /finance/portfolio/<uuid> and ./portfolio/<uuid>
        if "/portfolio/" not in href:
            continue
        # Resolve to absolute URL
        if href.startswith("http"):
            full = href
        elif href.startswith("/"):
            full = current_origin + href
        else:
            # relative like ./portfolio/<uuid>  →  resolve against GF_PORTFOLIO base
            full = current_origin + "/finance/" + href.lstrip("./")

        # Exclude watchlist (not a portfolio)
        if full.rstrip("/").endswith("/watchlist"):
            continue
        # Must have a UUID-like path segment after /portfolio/
        if full.rstrip("/").endswith("/portfolio"):
            continue

        text = (await link.inner_text()).strip()
        # Strip icon characters (Material icons like insert_chart) from the name
        text = " ".join(
            w for w in text.splitlines()
            if w.strip()
            and not w.strip().startswith("insert_")  # Material icon text
            and len(w.strip()) > 1
            and not w.strip().isdigit()               # count badge ("0", "20")
        ).strip()
        if text:
            result[text] = full
    return result


async def _create_portfolio(page: Page, name: str, emit) -> str:
    """
    Create a new portfolio and return its URL.

    New flow (2026+, verified via debug_create_portfolio.py):
      1. Click "New portfolio" button — Google Finance immediately creates an
         unnamed portfolio and redirects to /finance/portfolio/<uuid> (no dialog).
      2. Click "More menu options" (more_vert button in portfolio header).
      3. Click "Rename" in the dropdown menu.
      4. Fill the name input in the dialog and save.

    Old flow (kept as fallback if a name-input dialog appears):
      1. Click "New portfolio"
      2. Fill first enabled <input> in the dialog
      3. Click Save → redirects to /finance/portfolio/<uuid>
    """
    await page.goto(GF_PORTFOLIO, wait_until="domcontentloaded", timeout=20_000)
    await asyncio.sleep(1)  # brief settle — networkidle never fires on GF

    # NEW UI (2026-03-30): must switch to Portfolios tab first — otherwise the
    # page shows Watchlists and any "New" button creates a watchlist, not a portfolio.
    await _switch_to_portfolios_tab(page)

    await emit(Event("info", f"Creating portfolio '{name}'…"))

    # Click "New portfolio" — scoped to text that is unambiguously about portfolios.
    # NEVER fall back to div.VfPpkd-RLmnJb (matches the watchlist "New" button too).
    clicked = False
    for sel in [
        'span.VfPpkd-vQzf8d:has-text("New portfolio")',
        'button:has-text("New portfolio")',
        'a:has-text("New portfolio")',
        '[role="button"]:has-text("New portfolio")',
    ]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            clicked = True
            break

    if not clicked:
        for label in ["Create portfolio", "New portfolio"]:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click()
                clicked = True
                break

    if not clicked:
        raise RuntimeError(
            "Could not find 'New portfolio' button after switching to Portfolios tab. "
            "Run demo_logger.py and perform the create-portfolio flow to update selectors."
        )

    # ── Guard: "Create a new list/watchlist" dialog = wrong button clicked ────
    await asyncio.sleep(0.8)
    wrong_dialog = page.locator('[role="dialog"]').filter(
        has=page.locator('*:has-text("Create a new list"), *:has-text("Create a new watchlist")')
    )
    if await wrong_dialog.count() > 0:
        cancel = page.get_by_role("button", name="Cancel")
        if await cancel.count() > 0:
            await cancel.first.click()
        raise RuntimeError(
            "Wrong dialog opened — automation clicked the watchlist button instead of "
            "'New portfolio'. Run demo_logger.py to capture the correct selector."
        )

    # ── New behavior: page redirects directly without a naming dialog ────────
    # Check within 15 s whether the page navigated to a portfolio UUID URL.
    redirected = False
    try:
        await page.wait_for_url(
            lambda u: "/finance/portfolio/" in u and len(u.split("/")) > 5,
            timeout=15_000,
        )
        redirected = True
    except PWTimeout:
        pass

    # Also treat it as a redirect if the URL already matches (race condition)
    if not redirected:
        current = page.url
        if "/finance/portfolio/" in current and len(current.split("/")) > 5:
            redirected = True

    if redirected:
        # Wait for the portfolio header button — signals page is interactive.
        # networkidle never fires on GF due to persistent background requests.
        try:
            await page.wait_for_selector(
                'button[aria-label="More menu options"], button[aria-label="Add investment"]',
                timeout=15_000,
            )
        except PWTimeout:
            await asyncio.sleep(2)  # last-resort settle
        url = page.url
        # Rename the newly-created portfolio to the desired name
        await _rename_portfolio(page, name, emit)
        await emit(Event("success", f"Portfolio '{name}' created → {url}"))
        return url

    # ── Old behavior: a dialog with a name input appeared ───────────────────
    await page.wait_for_selector("input:not([disabled])", timeout=10_000)
    # Scope to the dialog if one exists, otherwise fall back to page-level
    dialog = page.locator('[role="dialog"]')
    if await dialog.count() > 0:
        name_input = dialog.locator("input:not([disabled])").first
    else:
        name_input = page.locator("input:not([disabled])").first
    await name_input.fill(name)
    await asyncio.sleep(0.3)

    save = page.locator("span.VfPpkd-vQzf8d", has_text="Save")
    if await save.count() == 0:
        save = page.get_by_role("button", name="Save")
    await save.first.click()

    await page.wait_for_url(
        lambda u: "/finance/portfolio/" in u and len(u.split("/")) > 5,
        timeout=15_000,
    )
    url = page.url
    await emit(Event("success", f"Portfolio '{name}' created → {url}"))
    return url


async def _rename_portfolio(page: Page, name: str, emit) -> None:
    """
    Rename the currently-open portfolio via More menu options → Rename.

    Observed on new portfolio page (debug_create_portfolio.py, 2026-03-29):
      button[aria-label="More menu options"] (more_vert icon) appears in the
      portfolio header. Clicking it opens a dropdown with a "Rename" item.
    """
    await emit(Event("info", f"Renaming portfolio to '{name}'…"))

    # "More menu options" button — pick the first one (sidebar lists many news ones)
    more_btn = page.get_by_role("button", name="More menu options")
    if await more_btn.count() == 0:
        await emit(Event("warning", "Cannot find 'More menu options' — portfolio keeps default name."))
        return

    await more_btn.first.click()
    await asyncio.sleep(0.5)

    # Click "Rename" in the dropdown
    rename_item = page.get_by_role("menuitem", name="Rename")
    if await rename_item.count() == 0:
        rename_item = page.locator('[role="menuitem"]', has_text="Rename")
    if await rename_item.count() == 0:
        rename_item = page.locator("li", has_text="Rename")
    if await rename_item.count() == 0:
        await emit(Event("warning", "Cannot find 'Rename' menu item — portfolio keeps default name."))
        return

    await rename_item.first.click()
    await asyncio.sleep(0.5)

    # Fill the name input (scoped to dialog if present)
    dialog = page.locator('[role="dialog"]')
    if await dialog.count() > 0:
        name_input = dialog.locator("input:not([disabled])").first
    else:
        name_input = page.locator("input:not([disabled])").first

    await name_input.wait_for(state="visible", timeout=8_000)
    await name_input.click(click_count=3)
    await name_input.fill(name)
    await asyncio.sleep(0.3)

    # Save
    for label in ["Save", "Rename", "Done"]:
        save = page.locator("span.VfPpkd-vQzf8d", has_text=label)
        if await save.count() > 0:
            await save.first.click()
            await asyncio.sleep(0.8)
            return
        save2 = page.get_by_role("button", name=label)
        if await save2.count() > 0:
            await save2.first.click()
            await asyncio.sleep(0.8)
            return


async def _open_portfolio(page: Page, name: str, emit) -> bool:
    """Navigate to a named portfolio. Returns True on success."""
    portfolios = await _get_portfolio_map(page)
    for p_name, p_url in portfolios.items():
        if p_name.lower() == name.lower():
            await page.goto(p_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(1)
            await emit(Event("info", f"Opened portfolio '{name}'."))
            return True
    return False


# ---------------------------------------------------------------------------
# Add-investment dialog helpers
# ---------------------------------------------------------------------------

async def _open_add_investment_dialog(page: Page) -> None:
    """
    Open the 'Add investment' dialog and verify the stock search input is present.

    Verified selectors (demo_log.jsonl 2026-03-31):
      Portfolio with holdings : div.a4CLte > ... > button.VfPpkd-LgbsSe
                                span text = "add\\nInvestment"
      Empty portfolio         : div.uFjxEd > ... > button.VfPpkd-LgbsSe
                                span text = "add\\nAdd investments"

    IMPORTANT — do NOT use has_text="Investment" without scoping to the container
    divs: the "Investments" tab button also matches that substring and appears
    first in the DOM, causing the wrong element to be clicked.
    """
    # Close any stray dialog / overlay that may be open
    for close_sel in ['button[aria-label="Close"]', 'button[aria-label="close"]']:
        cl = page.locator(close_sel)
        if await cl.count() > 0:
            try:
                await cl.first.click(timeout=2_000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

    # Wait for any lingering Material scrim/overlay to clear
    try:
        await page.locator("div.KL4X6e.TuA45b").wait_for(state="hidden", timeout=6_000)
    except PWTimeout:
        pass

    # Wait for the portfolio page to be ready — the button container must exist
    for sel in ["div.a4CLte", "div.uFjxEd"]:
        try:
            await page.locator(sel).wait_for(state="visible", timeout=8_000)
            break
        except PWTimeout:
            continue

    # ── Click the correct button ─────────────────────────────────────────────

    async def _try_click(loc) -> bool:
        if await loc.count() > 0:
            try:
                await loc.first.click(timeout=5_000)
            except PWTimeout:
                await loc.first.click(force=True)
            return True
        return False

    clicked = (
        # 1. Container-scoped — most reliable (verified 2026-03-31)
        await _try_click(page.locator("div.a4CLte button.VfPpkd-LgbsSe"))
        or await _try_click(page.locator("div.uFjxEd button.VfPpkd-LgbsSe"))
        # 2. Regex text match — "Investment" exact word, NOT "Investments"
        #    re.compile uses Python regex; \bInvestment\b won't match "Investments"
        or await _try_click(page.locator("button span.VfPpkd-vQzf8d",
                                         has_text=re.compile(r"\bInvestment\b")))
        or await _try_click(page.locator("button span.VfPpkd-vQzf8d",
                                         has_text=re.compile(r"Add investments", re.I)))
        # 3. aria-label fallbacks
        or await _try_click(page.locator('button[aria-label="Add investments"]'))
        or await _try_click(page.locator('button[aria-label="Investment"]'))
    )

    if not clicked:
        raise RuntimeError(
            "Could not find the '+ Investment' / 'Add investments' button. "
            "Run demo_logger.py to re-capture the selector."
        )

    # ── Verify the correct dialog opened ────────────────────────────────────
    # The investment dialog always contains the stock search input.
    # If something else opened (sort menu, portfolio breakdown, etc.) we close
    # it and raise so the retry loop can re-navigate and try again.
    search_input = page.locator('input[aria-label="Type an investment name or symbol"]')
    try:
        await search_input.wait_for(state="visible", timeout=8_000)
    except PWTimeout:
        # Wrong dialog — close whatever opened and fail so retry can recover
        for close_sel in [
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            'span.VfPpkd-vQzf8d:has-text("Close")',
            'span.VfPpkd-vQzf8d:has-text("Cancel")',
        ]:
            cl = page.locator(close_sel)
            if await cl.count() > 0:
                try:
                    await cl.first.click(timeout=2_000)
                except Exception:
                    pass
                break
        # Also press Escape to dismiss any overlay
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        raise RuntimeError(
            "Clicked a button but the stock search input never appeared — "
            "wrong dialog opened. Will retry."
        )

    await asyncio.sleep(0.3)


async def _select_stock_tab(page: Page) -> None:
    """
    Click the 'Stock' filter/tab in the add-investment dialog.

    New UI (2026+): filter chips are button elements with text labels
    (All / Stock / ETF / Index / Mutual fund / Currency).
    Old UI: div[role="tab"] with text="Stock".
    We try both; if neither is found we skip (default 'All' works for NSE stocks).
    """
    # New UI: button-based filter chips
    for sel in [
        'button[role="tab"]:has-text("Stock")',
        'button:has-text("Stock")',
    ]:
        tab = page.locator(sel)
        if await tab.count() > 0:
            await tab.first.click()
            await asyncio.sleep(0.6)
            return

    # Old UI: div[role="tab"] with text="Stock"
    tab = page.locator('div[role="tab"]', has_text="Stock")
    if await tab.count() > 0:
        await tab.first.click()
        await asyncio.sleep(0.8)


async def _search_and_select_stock(page: Page, query: str) -> None:
    """
    Type the ticker/symbol in the search box and select the best match.

    New UI (2026+): "Add to <Portfolio>" popup — search input is shown directly.
    After typing, a suggestion list appears. We prefer the NSE-listed result
    (subtitle contains ": NSE" or "NSE :"), then fall back to the first result.

    Observed selector: input[aria-label="Type an investment name or symbol"]
    Suggestion rows  : [role='listbox'] [role='option'], ul li, div.CrPloe
    """
    search = page.locator('input[aria-label="Type an investment name or symbol"]')
    if await search.count() == 0:
        search = page.get_by_placeholder("Type an investment name or symbol")
    if await search.count() == 0:
        search = page.get_by_placeholder("Search for a company, ticker")
    if await search.count() == 0:
        search = page.locator("input[type='search'], input[role='combobox']").first

    # Two-phase wait: if input still hidden after 5 s, re-click the Stock tab and retry.
    try:
        await search.wait_for(state="visible", timeout=5_000)
    except PWTimeout:
        await _select_stock_tab(page)
        await search.wait_for(state="visible", timeout=15_000)

    await search.fill(query)
    await asyncio.sleep(1.8)  # wait for suggestions to load

    # ── Verified suggestion selectors (demo_log.jsonl 2026-03-31) ───────────
    # Suggestion rows observed:
    #   div.onRPD  — outer clickable row  (always present)
    #   div.CrPloe — inner content div    (child of div.onRPD)
    # Both contain full text: "Infosys Ltd\nINFY : NSE (IN)\n₹1,247.80\n..."
    # Strategy: wait for rows, prefer NSE in text, click outer row.

    await page.locator("div.onRPD, div.CrPloe, [role='listbox'] [role='option']").first.wait_for(
        state="visible", timeout=8_000
    )

    # 1. Prefer div.onRPD rows (outer clickable row) with NSE in text
    rows = page.locator("div.onRPD")
    if await rows.count() > 0:
        for item in await rows.all():
            txt = (await item.inner_text()).strip().upper()
            if "NSE" in txt:
                await item.click()
                await asyncio.sleep(0.8)
                return
        await rows.first.click()
        await asyncio.sleep(0.8)
        return

    # 2. Fallback: div.CrPloe (inner content)
    rows = page.locator("div.CrPloe")
    if await rows.count() > 0:
        for item in await rows.all():
            txt = (await item.inner_text()).strip().upper()
            if "NSE" in txt:
                await item.click()
                await asyncio.sleep(0.8)
                return
        await rows.first.click()
        await asyncio.sleep(0.8)
        return

    # 3. ARIA listbox options
    for sel in ["[role='listbox'] [role='option']", "ul[role='listbox'] li"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            for item in await loc.all():
                txt = (await item.inner_text()).strip().upper()
                if "NSE" in txt:
                    await item.click()
                    await asyncio.sleep(0.8)
                    return
            await loc.first.click()
            await asyncio.sleep(0.8)
            return

    raise RuntimeError(f"No suggestions found for query '{query}'")


async def _fill_quantity(page: Page, quantity: float) -> None:
    """
    Fill the Quantity field.

    Demo log (2026-03-30): the input has a dynamic id (e.g. #c140, #c367) but
    is associated with a <label>Quantity</label> via the `for` attribute.
    The field starts pre-filled with 0; clicking it and using fill() replaces
    the value cleanly. triple_click() is unreliable on the stepper widget.
    """
    qty = page.get_by_label("Quantity")
    if await qty.count() == 0:
        qty = page.get_by_label("Shares")
    if await qty.count() == 0:
        qty = page.locator("input[aria-label*='uantit'], input[aria-label*='hare']").first
    if await qty.count() == 0:
        # Last resort: first number input in the dialog (quantity is always first)
        qty = page.locator('[role="dialog"] input[type="number"]').first

    await qty.wait_for(state="visible", timeout=8_000)
    await qty.click()
    await asyncio.sleep(0.2)
    await qty.fill(str(quantity))


async def _select_calendar_date(page: Page, date_str: str) -> None:
    """
    Select a date in the Google Finance calendar picker.

    date_str format: MM/DD/YY  (e.g. "01/15/24")

    Observed flow:
      1. Click input.whsOnd.zHQkBf  → calendar opens
      2. Navigate months if needed  (prev/next arrow buttons)
      3. Click div[role="gridcell"] matching the target day number

    Calendar header shows "Month YYYY". Navigate buttons are aria-label
    "Previous month" / "Next month" (standard Material date picker).
    """
    # Parse target date
    target = datetime.strptime(date_str, "%m/%d/%y")
    target_month = target.month
    target_year  = target.year
    target_day   = target.day

    # Click the date input to open calendar
    date_input = page.locator("input.whsOnd.zHQkBf")
    if await date_input.count() == 0:
        date_input = page.locator("input[type='date'], input[aria-label*='ate']").first

    await date_input.wait_for(state="visible", timeout=8_000)
    await date_input.click()
    await asyncio.sleep(0.5)

    # Navigate to the correct month (max 48 month steps)
    for _ in range(48):
        # Read the calendar header text to get current month/year
        header = page.locator('[role="dialog"] [aria-live="polite"], .VfPpkd-uITcZb-LgbsSe-bN97Pc')
        header_text = ""
        if await header.count() > 0:
            header_text = (await header.first.inner_text()).strip()

        if header_text:
            try:
                shown = datetime.strptime(header_text, "%B %Y")
                if shown.year == target_year and shown.month == target_month:
                    break
                # Decide direction
                if (shown.year, shown.month) > (target_year, target_month):
                    nav = page.get_by_role("button", name="Previous month")
                else:
                    nav = page.get_by_role("button", name="Next month")
                await nav.click()
                await asyncio.sleep(0.3)
            except ValueError:
                break  # Couldn't parse header — give up navigating
        else:
            break

    # Click the correct day cell
    day_cell = page.locator(f'div[role="gridcell"]', has_text=str(target_day))
    # Filter to exact text match so day 8 doesn't match 18/28
    for cell in await day_cell.all():
        txt = (await cell.inner_text()).strip()
        if txt == str(target_day):
            await cell.click()
            await asyncio.sleep(0.4)
            return

    # Fallback: click first matching cell
    await day_cell.first.click()
    await asyncio.sleep(0.4)


async def _fill_price(page: Page, price: float) -> None:
    """
    Fill the Purchase price / Price per share field.

    Demo log (2026-03-30): input has dynamic id (e.g. #c143, #c370), is
    pre-filled with the current market price.  User triple-clicked to select
    all then typed.  We replicate this with triple_click() + fill().
    """
    price_input = page.get_by_label("Purchase price")
    if await price_input.count() == 0:
        price_input = page.get_by_label("Price per share")
    if await price_input.count() == 0:
        price_input = page.get_by_label("Price")
    if await price_input.count() == 0:
        price_input = page.locator("input[aria-label*='rice']").first
    if await price_input.count() == 0:
        # Last resort: second visible text input in the dialog (after quantity)
        price_input = page.locator('[role="dialog"] input[type="text"]').last

    await price_input.wait_for(state="visible", timeout=8_000)
    await price_input.click(click_count=3)
    await asyncio.sleep(0.2)
    await price_input.fill(str(price))


async def _save_transaction(page: Page) -> None:
    """
    Save the transaction and close the dialog (last trade in a batch).
    Clicks plain 'Save', explicitly avoiding 'Save and add another'.
    """
    dialog = page.locator('[role="dialog"]')
    scope = dialog if await dialog.count() > 0 else page

    for label in ["Save", "Done", "Add"]:
        btn = scope.locator("span.VfPpkd-vQzf8d", has_text=label).filter(
            has_not_text="and add another"
        )
        if await btn.count() > 0:
            await btn.first.click()
            await asyncio.sleep(1.5)
            return
        btn2 = page.get_by_role("button", name=label, exact=True)
        if await btn2.count() > 0:
            await btn2.first.click()
            await asyncio.sleep(1.5)
            return

    raise RuntimeError("Could not find Save/Done button in transaction dialog.")


async def _save_and_add_another(page: Page) -> None:
    """
    Save the current trade and keep the dialog open for the next one.

    Verified (demo_log.jsonl 2026-03-31):
      span text : "Save and add another"
      path      : dialog footer > button.VfPpkd-LgbsSe > span.VfPpkd-vQzf8d
                  OR > div.VfPpkd-RLmnJb  (ripple click also works)

    After clicking, the dialog resets and auto-focuses the stock search input.
    """
    dialog = page.locator('[role="dialog"]')
    scope = dialog if await dialog.count() > 0 else page

    btn = scope.locator("span.VfPpkd-vQzf8d", has_text="Save and add another")
    if await btn.count() == 0:
        btn = page.get_by_role("button", name="Save and add another")

    if await btn.count() > 0:
        await btn.first.click()
    else:
        raise RuntimeError("Could not find 'Save and add another' button.")

    # Wait for dialog to reset — search input should be visible and empty
    search = page.locator('input[aria-label="Type an investment name or symbol"]')
    await search.wait_for(state="visible", timeout=8_000)
    await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# Per-trade processing
# ---------------------------------------------------------------------------

async def _process_trade(
    page: Page,
    trade: Trade,
    emit,
    *,
    dialog_already_open: bool = False,
    keep_open: bool = False,
) -> None:
    """
    Fill and save one BUY trade inside the Add investment dialog.

    dialog_already_open : True when the dialog is already showing after a
                          previous "Save and add another" click — skip opening.
    keep_open           : True for all trades except the last — click
                          "Save and add another" instead of "Save".
    """
    if not dialog_already_open:
        await emit(Event("info", "Opening Add investment dialog…", row=trade.row))
        await _open_add_investment_dialog(page)
        await _select_stock_tab(page)

    await emit(Event("info", f"Searching for '{trade.symbol}'…", row=trade.row))
    await _search_and_select_stock(page, trade.symbol)

    await emit(Event("info",
        f"Filling form: qty={trade.quantity}, date={trade.buy_date}, price={trade.buy_price}",
        row=trade.row))
    await _fill_quantity(page, trade.quantity)
    await _select_calendar_date(page, trade.buy_date)
    await _fill_price(page, trade.buy_price)

    if keep_open:
        await _save_and_add_another(page)
    else:
        await _save_transaction(page)

    await emit(Event("success",
        f"BUY saved — {trade.symbol} x{trade.quantity} @ {trade.buy_price}",
        row=trade.row))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_automation(
    trades: list[Trade],
    portfolio_name: str,
    dry_run: bool = False,
    headless: bool = False,
    create_if_missing: bool = True,
) -> AsyncGenerator[Event, None]:
    """
    Main entry point: resolves ISINs, launches browser, fills Google Finance.

    Playwright is run inside a dedicated thread that owns a fresh
    ProactorEventLoop.  This sidesteps the Windows restriction where
    SelectorEventLoop (which uvicorn may install) raises NotImplementedError
    when Playwright calls asyncio.create_subprocess_exec to start the browser.

    Yields Event objects that the FastAPI SSE endpoint streams to the browser.
    """
    ev_queue: _queue.Queue = _queue.Queue()

    def run_in_thread() -> None:
        # On Windows Playwright needs ProactorEventLoop to spawn the browser
        # subprocess.  Creating a fresh loop per-thread guarantees this
        # regardless of what event loop uvicorn chose for the main thread.
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _run() -> None:
            async def emit(event: Event) -> None:
                ev_queue.put(event)

            try:
                # ── Resolve all symbols upfront ──────────────────────────────
                _isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
                await emit(Event("info", f"Resolving {len(trades)} symbol(s)…"))
                for trade in trades:
                    if not trade.isin:
                        # Stock-name format: symbol already resolved by main.py
                        exch = getattr(trade, "exchange", "NSE") or "NSE"
                        await emit(Event("info", f"  {trade.symbol} ({exch}) — name resolved", row=trade.row))
                    elif trade.symbol and trade.symbol != trade.isin and not _isin_pattern.match(trade.symbol):
                        await emit(Event("info", f"  {trade.isin} → {trade.symbol} (from file)", row=trade.row))
                    else:
                        trade.symbol = resolve(trade.isin)
                        await emit(Event("info", f"  {trade.isin} → {trade.symbol}", row=trade.row))

                if dry_run:
                    await emit(Event("info", "Dry-run mode — skipping browser automation."))
                    await emit(Event("info", f"Net positions after FIFO: {len(trades)} buy lot(s) to enter into Google Finance."))
                    for t in trades:
                        label = t.symbol if not t.isin else f"{t.isin} → {t.symbol}"
                        await emit(Event("success",
                            f"[dry-run] {label}, {t.quantity} shares @ ₹{t.buy_price}  (buy date: {t.buy_date})",
                            row=t.row))
                    await emit(Event("done", "Dry run complete."))
                    return

                # ── Launch browser ───────────────────────────────────────────
                async with async_playwright() as pw:
                    browser, context = await _launch_context(pw, headless=headless)

                    page = await context.new_page()

                    try:
                        # ── Auth ─────────────────────────────────────────────
                        await emit(Event("info", "Checking authentication…"))
                        logged_in = await _is_logged_in(page)

                        if not logged_in:
                            await emit(Event("warning", "Not logged in. Opening browser for manual login (3 min timeout)…"))
                            await _manual_login(page)
                            await context.storage_state(path=str(AUTH_STATE))
                            await emit(Event("info", "Login saved — future runs will be automatic."))
                        else:
                            await emit(Event("success", "Already authenticated."))

                        # ── Open / create portfolio ───────────────────────────
                        await emit(Event("info", f"Looking for portfolio '{portfolio_name}'…"))
                        found = await _open_portfolio(page, portfolio_name, emit)

                        if not found:
                            if create_if_missing:
                                await _create_portfolio(page, portfolio_name, emit)
                            else:
                                await emit(Event("error", f"Portfolio '{portfolio_name}' not found and create_if_missing=False."))
                                return

                        # ── Process trades ────────────────────────────────────
                        # Strategy (verified 2026-03-31):
                        #   Open the dialog ONCE. For every trade except the last,
                        #   click "Save and add another" — the dialog resets and stays
                        #   open, ready for the next stock. Click plain "Save" on the
                        #   last trade to close the dialog.
                        #   On failure: dismiss dialog, re-navigate, re-open, retry.
                        total = len(trades)
                        failed: list[tuple[int, str, str]] = []

                        # Remaining trades to process (allows skipping on fatal failure)
                        pending = list(enumerate(trades, start=1))
                        dialog_open = False  # tracks whether dialog is currently open

                        while pending:
                            idx, trade = pending[0]
                            is_last = (len(pending) == 1)

                            await emit(Event("info",
                                f"[{idx}/{total}] {trade.symbol} (row {trade.row})…",
                                row=trade.row))

                            succeeded = False
                            for attempt in range(1, 4):
                                try:
                                    await _process_trade(
                                        page, trade, emit,
                                        dialog_already_open=dialog_open,
                                        keep_open=not is_last,
                                    )
                                    dialog_open = not is_last
                                    succeeded = True
                                    break
                                except Exception as e:
                                    dialog_open = False
                                    # Dismiss any open dialog / overlay
                                    await page.keyboard.press("Escape")
                                    await asyncio.sleep(1)
                                    if attempt < 3:
                                        await emit(Event("warning",
                                            f"Row {trade.row}: attempt {attempt} failed ({e}), retrying…",
                                            row=trade.row))
                                        await asyncio.sleep(2)
                                        await _open_portfolio(page, portfolio_name, emit)
                                        # Re-open dialog so next attempt finds it ready
                                        try:
                                            await _open_add_investment_dialog(page)
                                            await _select_stock_tab(page)
                                            dialog_open = True
                                        except Exception:
                                            dialog_open = False
                                    else:
                                        reason = str(e)
                                        failed.append((trade.row, trade.symbol, reason))
                                        await emit(Event("error",
                                            f"SKIPPED row {trade.row} ({trade.symbol}) after 3 attempts — {reason}",
                                            row=trade.row, detail=reason))
                                        # Re-open dialog for remaining trades if any
                                        if len(pending) > 1:
                                            try:
                                                await _open_portfolio(page, portfolio_name, emit)
                                                await _open_add_investment_dialog(page)
                                                await _select_stock_tab(page)
                                                dialog_open = True
                                            except Exception:
                                                dialog_open = False

                            pending.pop(0)  # move to next trade regardless

                        await context.storage_state(path=str(AUTH_STATE))

                        entered = total - len(failed)
                        if failed:
                            await emit(Event("warning",
                                f"{len(failed)} trade(s) skipped due to errors — see log above for details."))
                        await emit(Event("done",
                            f"{entered}/{total} trade(s) entered into '{portfolio_name}'."
                            + (f" {len(failed)} skipped." if failed else "")))

                    finally:
                        await context.close()
                        await browser.close()

            except Exception as e:
                await emit(Event("error", f"Automation crashed: {e}", detail=str(e)))
                await emit(Event("done", "Automation ended with errors."))

        try:
            loop.run_until_complete(_run())
        finally:
            ev_queue.put(None)  # sentinel — always signals the generator to stop
            loop.close()

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

    # Bridge thread-safe queue → async generator.
    # asyncio.to_thread wraps the blocking queue.get() so the uvicorn event
    # loop stays responsive while waiting for the next automation event.
    while True:
        event = await asyncio.to_thread(lambda: ev_queue.get(timeout=600))
        if event is None:
            break
        yield event

    thread.join(timeout=5)
