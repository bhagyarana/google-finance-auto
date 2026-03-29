"""
Interactive demo logger for Google Finance automation.

Launches a visible Chromium browser, lets you perform actions manually,
and logs every click, input, and navigation to demo_log.jsonl in real-time.

Usage:
    .venv/Scripts/python.exe demo_logger.py

Stop with Ctrl+C when done. The log is written to demo_log.jsonl.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

LOG_FILE   = Path("demo_log.jsonl")
AUTH_STATE = Path("auth/gf_state.json")
GF_HOME    = "https://www.google.com/finance/portfolio"


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


# JavaScript injected into every page — captures clicks and input changes
_JS_LOGGER = """
(function() {
  if (window.__demoLoggerAttached) return;
  window.__demoLoggerAttached = true;

  function bestSelector(el) {
    if (!el) return '(none)';
    const tag  = el.tagName.toLowerCase();
    const id   = el.id   ? '#' + el.id   : '';
    const role = el.getAttribute('role') ? '[role="' + el.getAttribute('role') + '"]' : '';
    const name = el.getAttribute('name') ? '[name="' + el.getAttribute('name') + '"]' : '';
    const aria = el.getAttribute('aria-label') ? '[aria-label="' + el.getAttribute('aria-label') + '"]' : '';
    const ph   = el.getAttribute('placeholder') ? '[placeholder="' + el.getAttribute('placeholder') + '"]' : '';
    const cls  = el.className && typeof el.className === 'string'
                   ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '';
    return tag + (id || aria || ph || role || name || cls || '');
  }

  function getText(el) {
    return (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80);
  }

  document.addEventListener('click', function(e) {
    const el = e.target;
    window.__demoLog({ kind:'click', selector: bestSelector(el), text: getText(el) });
  }, true);

  document.addEventListener('change', function(e) {
    const el = e.target;
    window.__demoLog({ kind:'input_change', selector: bestSelector(el), value: el.value, text: getText(el) });
  }, true);

  document.addEventListener('input', function(e) {
    const el = e.target;
    window.__demoLog({ kind:'input', selector: bestSelector(el), value: el.value });
  }, true);
})();
"""


async def main():
    LOG_FILE.unlink(missing_ok=True)
    print(f"Demo logger started. Log -> {LOG_FILE.resolve()}")
    print("Perform your actions in the browser window. Press Ctrl+C here to stop.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            slow_mo=0,
            args=["--disable-blink-features=AutomationControlled"],
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

        # Inject logger JS on every new page/frame
        await context.add_init_script(_JS_LOGGER)

        page = await context.new_page()

        # Expose a Python function so JS can call window.__demoLog(...)
        async def js_log(entry: dict) -> None:
            entry["ts"] = _ts()
            _write(entry)

        await context.expose_function("__demoLog", js_log)

        # Log navigations
        def on_nav(frame):
            if frame == page.main_frame:
                _write({"ts": _ts(), "kind": "navigate", "url": page.url})

        page.on("framenavigated", on_nav)

        # Log network requests to Google Finance API endpoints
        def on_request(req):
            url = req.url
            if "finance" in url or "google.com" in url:
                if req.method in ("POST", "PUT", "PATCH"):
                    _write({"ts": _ts(), "kind": "xhr_" + req.method.lower(), "url": url})

        page.on("request", on_request)

        await page.goto(GF_HOME, wait_until="domcontentloaded")

        print("Browser open. Go ahead — add your transaction manually.\n")

        # Keep running until user presses Ctrl+C or closes browser
        try:
            while not page.is_closed():
                await asyncio.sleep(0.5)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            # Save auth state if logged in
            try:
                AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(AUTH_STATE))
                print(f"\nAuth state saved -> {AUTH_STATE}")
            except Exception:
                pass
            await browser.close()

    print(f"\nDone. {LOG_FILE.stat().st_size} bytes written to {LOG_FILE}")
    print("Share the log and I'll update the automation selectors.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
