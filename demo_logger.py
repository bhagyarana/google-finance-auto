"""
Interactive demo logger for Google Finance automation.

Launches a real Chrome browser with your saved login, navigates to the
portfolio you specify, and records every click + input to demo_log.jsonl.

Usage:
    .venv\\Scripts\\python demo_logger.py [portfolio-name]

    Examples:
        .venv\\Scripts\\python demo_logger.py
        .venv\\Scripts\\python demo_logger.py "My Portfolio"

Stop with Ctrl+C (or just close the browser). Log is written to demo_log.jsonl.

WHAT TO RECORD
--------------
The automation needs you to manually perform the FULL "add investment" flow
on a portfolio that ALREADY HAS at least one stock in it (so the page shows
the blue "+ Investment" button, not the empty-state "Add investments" button).

Step-by-step:
  1. The browser opens on the portfolio page.
  2. Click the blue "+ Investment" button (top-right of the investments table).
  3. In the dialog: type a stock name / ticker in the search box.
  4. Select the NSE result from the dropdown.
  5. Fill in Quantity, Date (use the calendar), Purchase price.
  6. Click Save.
  7. Press Ctrl+C in this terminal to stop and save the log.

Then share demo_log.jsonl and I'll update the selectors.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

LOG_FILE   = Path("demo_log.jsonl")
AUTH_STATE = Path("auth/gf_state.json")
GF_PORTFOLIO = "https://www.google.com/finance/portfolio"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _write(entry: dict) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    kind  = entry.get("kind", "?").upper()
    value = entry.get("value", "")
    url   = entry.get("url", "")
    sel   = entry.get("selector", "")
    text  = entry.get("text", "")
    ts    = entry["ts"]

    parts = [f"[{ts}] [{kind}]"]
    if url:   parts.append(f"  url     : {url}")
    if sel:   parts.append(f"  selector: {sel}")
    if text:  parts.append(f"  text    : {text!r}")
    if value: parts.append(f"  value   : {value!r}")
    print("\n".join(parts))
    print()


# Injected into every page — captures clicks, inputs, and focused elements
_JS_LOGGER = r"""
(function() {
  if (window.__demoLoggerAttached) return;
  window.__demoLoggerAttached = true;

  function bestSelector(el) {
    if (!el) return '(none)';
    const tag   = el.tagName.toLowerCase();
    const id    = el.id    ? '#' + el.id   : '';
    const role  = el.getAttribute('role')       ? '[role="'        + el.getAttribute('role')        + '"]' : '';
    const aria  = el.getAttribute('aria-label') ? '[aria-label="'  + el.getAttribute('aria-label')  + '"]' : '';
    const ph    = el.getAttribute('placeholder')? '[placeholder="' + el.getAttribute('placeholder') + '"]' : '';
    const name  = el.getAttribute('name')       ? '[name="'        + el.getAttribute('name')        + '"]' : '';
    const cls   = el.className && typeof el.className === 'string'
                    ? '.' + el.className.trim().split(/\s+/).slice(0,3).join('.') : '';
    // Also capture the parent button's aria-label if this element is a child
    const parent = el.closest('button');
    const btnAria = parent && parent !== el && parent.getAttribute('aria-label')
                    ? ' (btn[aria-label="' + parent.getAttribute('aria-label') + '"])' : '';
    return tag + (id || aria || ph || role || name || cls || '') + btnAria;
  }

  function getText(el) {
    // Walk up to nearest button/anchor to get meaningful label
    const container = el.closest('button, a, [role="option"], [role="menuitem"], label') || el;
    return (container.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 120);
  }

  function getFullPath(el) {
    const parts = [];
    let node = el;
    while (node && node !== document.body) {
      let seg = node.tagName ? node.tagName.toLowerCase() : '';
      if (node.id) seg += '#' + node.id;
      else if (node.className && typeof node.className === 'string')
        seg += '.' + node.className.trim().split(/\s+/)[0];
      parts.unshift(seg);
      node = node.parentElement;
      if (parts.length > 6) break;
    }
    return parts.join(' > ');
  }

  document.addEventListener('click', function(e) {
    const el = e.target;
    window.__demoLog({
      kind:     'click',
      selector: bestSelector(el),
      text:     getText(el),
      path:     getFullPath(el),
    });
  }, true);

  document.addEventListener('change', function(e) {
    const el = e.target;
    window.__demoLog({
      kind:     'input_change',
      selector: bestSelector(el),
      value:    el.value,
      text:     getText(el),
    });
  }, true);

  document.addEventListener('input', function(e) {
    const el = e.target;
    // Only log when the user pauses (debounce-like: log every 500ms of typing)
    clearTimeout(el.__logTimer);
    el.__logTimer = setTimeout(function() {
      window.__demoLog({ kind: 'input_value', selector: bestSelector(el), value: el.value });
    }, 500);
  }, true);

  document.addEventListener('focusin', function(e) {
    const el = e.target;
    if (['input', 'textarea', 'select'].includes(el.tagName.toLowerCase())) {
      window.__demoLog({ kind: 'focus', selector: bestSelector(el), text: getText(el) });
    }
  }, true);
})();
"""


async def main():
    portfolio_name = sys.argv[1] if len(sys.argv) > 1 else None

    LOG_FILE.unlink(missing_ok=True)
    print("=" * 60)
    print("  Google Finance Demo Logger")
    print("=" * 60)
    print(f"  Log file : {LOG_FILE.resolve()}")
    if portfolio_name:
        print(f"  Portfolio: {portfolio_name}")
    print()
    print("INSTRUCTIONS")
    print("------------")
    print("1. The browser will open on your portfolio page.")
    print("2. Click the blue '+ Investment' button (top-right).")
    print("3. Complete the full add-investment flow for ONE stock.")
    print("4. Press Ctrl+C here when done to save the log.")
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            slow_mo=0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-infobars",
            ],
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

        await context.add_init_script(_JS_LOGGER)
        page = await context.new_page()

        # Expose Python callback so JS can call window.__demoLog(...)
        async def js_log(entry: dict) -> None:
            entry["ts"] = _ts()
            _write(entry)

        await context.expose_function("__demoLog", js_log)

        # Log navigations
        def on_nav(frame):
            if frame == page.main_frame:
                _write({"ts": _ts(), "kind": "navigate", "url": page.url})

        page.on("framenavigated", on_nav)

        # Navigate to the portfolio
        target_url = GF_PORTFOLIO
        await page.goto(target_url, wait_until="domcontentloaded")

        # If a specific portfolio name was given, try to navigate to it
        if portfolio_name:
            await asyncio.sleep(2)
            # Try clicking the tab
            tab = page.locator(f'[role="tab"]', has_text=portfolio_name)
            if await tab.count() == 0:
                tab = page.get_by_role("link", name=portfolio_name)
            if await tab.count() > 0:
                await tab.first.click()
                await asyncio.sleep(1.5)
                print(f"  Navigated to portfolio: {portfolio_name}")
            else:
                print(f"  Could not find tab for '{portfolio_name}' — proceeding on current page.")

        print(f"\nBrowser open at: {page.url}")
        print("Go ahead — perform the add-investment flow now.\n")

        try:
            while not page.is_closed():
                await asyncio.sleep(0.5)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            try:
                AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(AUTH_STATE))
                print(f"\nAuth state saved -> {AUTH_STATE}")
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
        print(f"\nDone. {len(lines)} events recorded -> {LOG_FILE.resolve()}")
        print("\nShare demo_log.jsonl and I will update the automation selectors.")
    else:
        print("\nNo events recorded.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
