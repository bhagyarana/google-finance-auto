"""
ISIN → NSE ticker symbol resolver.

Resolution order:
  1. In-memory cache (fast, zero I/O)
  2. Persistent JSON cache on disk (survives restarts)
  3. Groww public search API (network call, result saved to both caches)
"""

import json
import os
import requests
from pathlib import Path

CACHE_PATH = Path(__file__).parent.parent / "data" / "isin_cache.json"

# In-memory cache loaded once at import time
_cache: dict[str, str] = {}


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
        CACHE_PATH.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[isin_resolver] Could not save cache: {e}")


def _fetch_from_groww(isin: str) -> str | None:
    """Call Groww public search API to resolve an ISIN to an NSE symbol."""
    try:
        url = (
            "https://groww.in/v1/api/search/v3/query/global/st_p_query"
            f"?page=0&query={isin}&size=10&web=true"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("data", {}).get("content", [])
        if content:
            symbol = content[0].get("nse_scrip_code") or content[0].get("bse_scrip_code")
            return symbol if symbol and symbol != "N/A" else None
    except Exception as e:
        print(f"[isin_resolver] Groww API error for {isin}: {e}")
    return None


def resolve(isin: str) -> str:
    """
    Resolve an ISIN to its NSE ticker symbol.
    Returns the original ISIN if resolution fails (Google Finance also accepts ISINs).
    """
    if not _cache:
        _load_cache()

    isin = isin.strip().upper()

    if isin in _cache:
        return _cache[isin]

    symbol = _fetch_from_groww(isin)
    if symbol:
        _cache[isin] = symbol
        _save_cache()
        return symbol

    # Fallback: return ISIN itself (Google Finance search can handle ISINs too)
    print(f"[isin_resolver] Could not resolve {isin}, using ISIN as-is")
    return isin


def resolve_batch(isins: list[str]) -> dict[str, str]:
    """Resolve a list of ISINs, returning a mapping {isin: symbol}."""
    if not _cache:
        _load_cache()
    return {isin: resolve(isin) for isin in isins}


# Preload cache on module import
_load_cache()
