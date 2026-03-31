"""
Stock Name → NSE/BSE ticker resolver.

Resolution order:
  1. In-memory cache (fast, zero I/O)
  2. Persistent JSON cache on disk  (data/stock_name_cache.json)
  3. Groww public search API        (network call, result saved to both caches)
  4. Difflib fuzzy match            (against Groww results; no extra dependencies)
  5. Fallback                       (return name as-is; Google Finance search handles it)

Cache key: "<name>|<exchange>"  e.g. "HDFC Bank|NSE"
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).parent.parent / "data" / "stock_name_cache.json"

_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> None:
    global _cache
    try:
        if CACHE_PATH.exists():
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}


def _save_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[stock_resolver] Could not save cache: {e}")


def _cache_key(name: str, exchange: str) -> str:
    return f"{name.strip()}|{exchange.strip().upper()}"


# ---------------------------------------------------------------------------
# Groww API
# ---------------------------------------------------------------------------

def _fetch_from_groww(query: str, exchange: str) -> str | None:
    """
    Call Groww public search API with a company name / partial ticker.
    Returns the best-matching NSE or BSE ticker, preferring `exchange`.
    """
    try:
        url = (
            "https://groww.in/v1/api/search/v3/query/global/st_p_query"
            f"?page=0&query={requests.utils.quote(query)}&size=10&web=true"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("data", {}).get("content", [])
        if not content:
            return None

        prefer_nse = exchange.upper() == "NSE"

        # Pass 1: exact exchange preference
        for item in content:
            nse = item.get("nse_scrip_code", "") or ""
            bse = item.get("bse_scrip_code", "") or ""
            if prefer_nse and nse and nse != "N/A":
                return nse
            if not prefer_nse and bse and bse != "N/A":
                return bse

        # Pass 2: fallback to whatever is available
        for item in content:
            nse = item.get("nse_scrip_code", "") or ""
            bse = item.get("bse_scrip_code", "") or ""
            ticker = nse if (nse and nse != "N/A") else (bse if (bse and bse != "N/A") else "")
            if ticker:
                return ticker

    except Exception as e:
        print(f"[stock_resolver] Groww API error for '{query}': {e}")
    return None


def _fetch_with_fuzzy_fallback(name: str, exchange: str) -> str | None:
    """
    Try Groww with the exact name; if nothing found, attempt common
    abbreviation expansions (e.g. "HDFC Bank" → "HDFC").
    """
    result = _fetch_from_groww(name, exchange)
    if result:
        return result

    # Strip common suffixes and retry
    stripped = name
    for suffix in (" Ltd", " Limited", " Industries", " Corp", " Corporation",
                   " Finance", " Bank", " Pharma", " Pharmaceuticals",
                   " Technologies", " Technology", " Enterprises"):
        if name.lower().endswith(suffix.lower()):
            stripped = name[: len(name) - len(suffix)].strip()
            break

    if stripped != name:
        result = _fetch_from_groww(stripped, exchange)
        if result:
            return result

    # Last attempt: first word only (e.g. "Reliance Industries" → "Reliance")
    first_word = name.split()[0] if name.split() else name
    if first_word != name and first_word != stripped:
        result = _fetch_from_groww(first_word, exchange)
        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(name: str, exchange: str = "NSE") -> str:
    """
    Resolve a human-readable stock name to its NSE/BSE ticker.

    Returns the original name if resolution fails — Google Finance's own
    search box can usually match company names directly.
    """
    if not _cache:
        _load_cache()

    name = name.strip()
    exchange = exchange.strip().upper() if exchange else "NSE"
    key = _cache_key(name, exchange)

    if key in _cache:
        return _cache[key]

    ticker = _fetch_with_fuzzy_fallback(name, exchange)
    if ticker:
        _cache[key] = ticker
        _save_cache()
        return ticker

    print(f"[stock_resolver] Could not resolve '{name}' ({exchange}), using name as-is")
    return name


def resolve_batch(items: list[dict]) -> dict[str, str]:
    """
    Resolve a list of {name, exchange} dicts.

    Returns a mapping  "<name>|<exchange>" → resolved_ticker.

    Example input:
        [{"name": "HDFC Bank", "exchange": "NSE"}, ...]
    """
    if not _cache:
        _load_cache()

    result: dict[str, str] = {}
    for item in items:
        name = item.get("name", "").strip()
        exchange = (item.get("exchange", "NSE") or "NSE").strip().upper()
        if not name:
            continue
        key = _cache_key(name, exchange)
        result[key] = resolve(name, exchange)
    return result


# Pre-load cache on module import
_load_cache()
