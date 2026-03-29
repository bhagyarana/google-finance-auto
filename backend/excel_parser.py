"""
Excel parser — auto-detects Tradebook (Zerodha) and Classic formats.
Returns (list[Trade], list[warnings]).
"""

from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class Trade:
    row: int
    isin: str
    symbol: str
    quantity: float
    buy_date: Optional[str]    # MM/DD/YY (Google Finance format); None for sell-only rows
    buy_price: Optional[float]
    sell_date: Optional[str]
    sell_price: Optional[float]
    has_sell: bool
    trade_type: str = "buy"    # "buy" | "sell"

    def to_dict(self) -> dict:
        return asdict(self)


_DATE_FORMATS = [
    "%Y-%m-%d",   # 2024-01-25  ← tradebook default
    "%d-%m-%Y",   # 25-01-2024
    "%d/%m/%Y",   # 25/01/2024
    "%m/%d/%Y",   # 01/25/2024
    "%d-%b-%Y",   # 25-Jan-2024
    "%Y%m%d",     # 20191129
]


def _parse_date(raw) -> Optional[str]:
    """Parse a date from various formats → MM/DD/YY (Google Finance format)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, datetime):
        return raw.strftime("%m/%d/%y")
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "nat", "none", ""):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%m/%d/%y")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {raw!r}")


def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _is_tradebook_format(columns: list[str]) -> bool:
    """True if sheet has a 'trade type' + single 'price' column (Zerodha tradebook style)."""
    normed = [_normalise(c) for c in columns]
    has_trade_type = any("trade type" in n or n == "trade_type" or n == "type" for n in normed)
    has_price      = any(n == "price" or n == "trade price" for n in normed)
    has_buy_price  = any("buy price" in n or "buy_price" in n or "purchase price" in n for n in normed)
    return has_trade_type and has_price and not has_buy_price


def _find_col(df: pd.DataFrame, *aliases: str) -> Optional[str]:
    """Return the first df column whose normalised name matches any alias."""
    for col in df.columns:
        n = _normalise(col)
        for alias in aliases:
            if alias in n:
                return col
    return None


class ParseError(Exception):
    pass


def _parse_tradebook(df: pd.DataFrame) -> tuple[list[Trade], list[str]]:
    col_symbol     = _find_col(df, "symbol")
    col_isin       = _find_col(df, "isin")
    col_date       = _find_col(df, "trade date", "trade_date", "date")
    col_trade_type = _find_col(df, "trade type", "trade_type", "type", "action")
    col_qty        = _find_col(df, "quantity", "qty", "shares")
    col_price      = _find_col(df, "price", "trade price")

    missing = [k for k, v in {
        "ISIN":       col_isin,
        "Trade Date": col_date,
        "Trade Type": col_trade_type,
        "Quantity":   col_qty,
        "Price":      col_price,
    }.items() if v is None]

    if missing:
        raise ParseError(
            f"Tradebook format detected but required columns missing: {missing}. "
            f"Available: {list(df.columns)}"
        )

    trades: list[Trade] = []
    errors: list[str] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # +1 for 0-index, +1 for header row → matches Excel row #

        def cell(col: Optional[str]):
            return row[col] if col and col in df.columns else None

        sym_raw = cell(col_symbol)
        symbol  = str(sym_raw).strip() if sym_raw and str(sym_raw).strip().lower() not in ("nan", "") else ""

        isin_raw = cell(col_isin)
        if not isin_raw or str(isin_raw).strip().lower() in ("nan", ""):
            errors.append(f"Row {row_num}: ISIN is empty, skipping.")
            continue
        isin = str(isin_raw).strip().upper()

        tt_raw = cell(col_trade_type)
        if not tt_raw or str(tt_raw).strip().lower() in ("nan", ""):
            errors.append(f"Row {row_num}: Trade Type is empty, skipping.")
            continue
        tt = str(tt_raw).strip().lower()
        if tt not in ("buy", "sell", "b", "s"):
            errors.append(f"Row {row_num}: Unknown Trade Type '{tt_raw}', skipping.")
            continue
        is_buy = tt in ("buy", "b")

        try:
            qty = float(str(cell(col_qty)).replace(",", ""))
            if qty <= 0:
                raise ValueError("non-positive")
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid quantity '{cell(col_qty)}', skipping.")
            continue

        try:
            price = float(str(cell(col_price)).replace(",", ""))
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid price '{cell(col_price)}', skipping.")
            continue

        try:
            trade_date = _parse_date(cell(col_date))
            if trade_date is None:
                raise ValueError("empty")
        except ValueError as e:
            errors.append(f"Row {row_num}: Invalid date '{cell(col_date)}' — {e}, skipping.")
            continue

        # Use symbol from column if present; otherwise fall back to ISIN
        resolved_symbol = symbol if symbol else isin

        if is_buy:
            trades.append(Trade(
                row        = row_num,
                isin       = isin,
                symbol     = resolved_symbol,
                quantity   = qty,
                buy_date   = trade_date,
                buy_price  = price,
                sell_date  = None,
                sell_price = None,
                has_sell   = False,
                trade_type = "buy",
            ))
        else:
            trades.append(Trade(
                row        = row_num,
                isin       = isin,
                symbol     = resolved_symbol,
                quantity   = qty,
                buy_date   = None,
                buy_price  = None,
                sell_date  = trade_date,
                sell_price = price,
                has_sell   = True,
                trade_type = "sell",
            ))

    return trades, errors


_CLASSIC_ALIASES: dict[str, list[str]] = {
    "isin":       ["isin", "symbol", "ticker", "stock"],
    "quantity":   ["quantity", "qty", "shares"],
    "buy_date":   ["buy date", "buy_date", "purchase date", "date", "transaction date"],
    "buy_price":  ["buy price", "buy_price", "purchase price", "cost price"],
    "sell_date":  ["sell date", "sell_date"],
    "sell_price": ["sell price", "sell_price"],
}


def _parse_classic(df: pd.DataFrame, skip_rows: int = 0) -> tuple[list[Trade], list[str]]:
    cols: dict[str, Optional[str]] = {}
    missing = []
    for key, aliases in _CLASSIC_ALIASES.items():
        col = _find_col(df, *aliases)
        cols[key] = col
        if col is None and key not in ("sell_date", "sell_price"):
            missing.append(key)

    if missing:
        raise ParseError(
            f"Required columns not found: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    trades: list[Trade] = []
    errors: list[str] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 1 + skip_rows

        def get(key: str):
            c = cols[key]
            return row[c] if c else None

        isin_raw = get("isin")
        if not isin_raw or str(isin_raw).strip().lower() in ("nan", ""):
            errors.append(f"Row {row_num}: ISIN is empty, skipping.")
            continue
        isin = str(isin_raw).strip().upper()

        try:
            qty = float(str(get("quantity")).replace(",", ""))
            if qty <= 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid quantity '{get('quantity')}', skipping.")
            continue

        sell_date:  Optional[str]   = None
        sell_price: Optional[float] = None
        has_sell = False

        sd_raw = get("sell_date")
        sp_raw = get("sell_price")

        if sd_raw and str(sd_raw).strip().lower() not in ("nan", "none", ""):
            try:
                sell_date = _parse_date(sd_raw)
            except ValueError as e:
                errors.append(f"Row {row_num}: Invalid sell date — {e}, ignoring sell.")

        if sp_raw and str(sp_raw).strip().lower() not in ("nan", "none", ""):
            try:
                sell_price = float(str(sp_raw).replace(",", ""))
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid sell price '{sp_raw}', ignoring sell.")

        if sell_date and sell_price is not None:
            has_sell = True

        bd_raw = get("buy_date")
        bp_raw = get("buy_price")
        buy_date_missing  = not bd_raw or str(bd_raw).strip().lower() in ("nan", "none", "")
        buy_price_missing = not bp_raw or str(bp_raw).strip().lower() in ("nan", "none", "")

        if buy_date_missing and buy_price_missing:
            if not has_sell:
                errors.append(f"Row {row_num}: Neither buy nor sell data found, skipping.")
                continue
            # Sell-only row
            trades.append(Trade(
                row=row_num, isin=isin, symbol=isin, quantity=qty,
                buy_date=None, buy_price=None,
                sell_date=sell_date, sell_price=sell_price,
                has_sell=True, trade_type="sell",
            ))
            continue

        if buy_date_missing:
            errors.append(f"Row {row_num}: Buy date is empty, skipping.")
            continue
        try:
            buy_date = _parse_date(bd_raw)
            if buy_date is None:
                raise ValueError("empty")
        except ValueError as e:
            errors.append(f"Row {row_num}: Invalid buy date — {e}, skipping.")
            continue

        if buy_price_missing:
            errors.append(f"Row {row_num}: Buy price is empty, skipping.")
            continue
        try:
            buy_price = float(str(bp_raw).replace(",", ""))
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid buy price '{bp_raw}', skipping.")
            continue

        trades.append(Trade(
            row=row_num, isin=isin, symbol=isin, quantity=qty,
            buy_date=buy_date, buy_price=buy_price,
            sell_date=sell_date, sell_price=sell_price,
            has_sell=has_sell, trade_type="buy",
        ))

    return trades, errors


def parse_excel(path: str | Path, skip_rows: int = 0) -> tuple[list[Trade], list[str]]:
    path = Path(path)
    if not path.exists():
        raise ParseError(f"File not found: {path}")

    try:
        df = pd.read_excel(path, skiprows=skip_rows, dtype=str)
    except Exception as e:
        raise ParseError(f"Could not read Excel file: {e}") from e

    df = df.dropna(how="all").reset_index(drop=True)

    if df.empty:
        raise ParseError("The spreadsheet contains no data rows.")

    if _is_tradebook_format(list(df.columns)):
        trades, errors = _parse_tradebook(df)
    else:
        trades, errors = _parse_classic(df, skip_rows=skip_rows)

    if errors:
        print("[excel_parser] Warnings during parse:")
        for e in errors:
            print(f"  {e}")

    if not trades:
        raise ParseError("No valid trades found after parsing. Check the file format.")

    return trades, errors
