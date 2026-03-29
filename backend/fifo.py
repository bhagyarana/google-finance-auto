"""FIFO accounting engine — nets sell transactions against buy lots before Google Finance upload."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from excel_parser import Trade


@dataclass
class FifoLot:
    """A surviving (unsold) buy lot after FIFO matching."""
    isin: str
    quantity: float
    buy_date: str    # MM/DD/YY
    buy_price: float
    source_row: int  # original spreadsheet row


@dataclass
class FifoSummary:
    """Per-ISIN summary of the FIFO calculation."""
    isin: str
    total_bought: float
    total_sold: float
    net_quantity: float
    realized_gain: float   # positive = profit, negative = loss

    def to_dict(self) -> dict:
        return {
            "isin":          self.isin,
            "total_bought":  self.total_bought,
            "total_sold":    self.total_sold,
            "net_quantity":  self.net_quantity,
            "realized_gain": self.realized_gain,
        }


@dataclass
class FifoResult:
    """Output of apply_fifo()."""
    net_trades: list[Trade]          # Trade objects ready for Google Finance
    summaries:  list[FifoSummary]    # per-ISIN accounting summary
    warnings:   list[str]            # oversell alerts, etc.



def _to_dt(date_str: Optional[str]) -> datetime:
    """Parse MM/DD/YY → datetime for chronological sorting."""
    if not date_str:
        return datetime.min
    try:
        return datetime.strptime(date_str, "%m/%d/%y")
    except ValueError:
        return datetime.min



def apply_fifo(raw_trades: list[Trade]) -> FifoResult:
    """Apply FIFO matching to raw buy/sell trades. Returns surviving buy lots ready for automation."""

    buy_lots:  dict[str, list[dict]] = defaultdict(list)
    sell_lots: dict[str, list[dict]] = defaultdict(list)

    for t in raw_trades:
        isin = t.isin

        if t.trade_type == "buy" and t.buy_date and t.buy_price is not None:
            buy_lots[isin].append({
                "dt":       _to_dt(t.buy_date),
                "price":    t.buy_price,
                "qty":      t.quantity,
                "row":      t.row,
                "date_str": t.buy_date,
                "symbol":   t.symbol,   # preserve pre-filled ticker from Excel
            })

        if t.has_sell and t.sell_date and t.sell_price is not None:
            sell_qty = t.quantity
            sell_lots[isin].append({
                "dt":    _to_dt(t.sell_date),
                "price": t.sell_price,
                "qty":   sell_qty,
                "row":   t.row,
            })

    for isin in buy_lots:
        buy_lots[isin].sort(key=lambda x: x["dt"])
    for isin in sell_lots:
        sell_lots[isin].sort(key=lambda x: x["dt"])

    net_trades: list[Trade] = []
    summaries:  list[FifoSummary] = []
    warnings:   list[str] = []

    all_isins = sorted(set(buy_lots.keys()) | set(sell_lots.keys()))

    for isin in all_isins:
        buys  = [dict(b) for b in buy_lots.get(isin, [])]   # mutable copies
        sells = sell_lots.get(isin, [])

        total_bought = sum(b["qty"] for b in buys)
        total_sold   = sum(s["qty"] for s in sells)

        if total_sold > total_bought:
            if total_bought == 0:
                warnings.append(
                    f"{isin}: {total_sold:.4g} share(s) sold but no buy history found in file "
                    f"— realized gain cannot be calculated; position excluded from GF upload."
                )
            else:
                warnings.append(
                    f"{isin}: sold {total_sold:.4g} shares but only {total_bought:.4g} were bought "
                    f"in file — {total_sold - total_bought:.4g} excess sell(s) ignored."
                )

        realized_gain = 0.0
        buy_queue = buys

        for sell in sells:
            remaining = sell["qty"]
            while remaining > 1e-9 and buy_queue:
                head = buy_queue[0]
                matched = min(remaining, head["qty"])
                realized_gain += (sell["price"] - head["price"]) * matched
                head["qty"]  -= matched
                remaining    -= matched
                if head["qty"] < 1e-9:
                    buy_queue.pop(0)

        for lot in buy_queue:
            if lot["qty"] < 1e-9:
                continue
            net_qty = round(lot["qty"], 10)
            net_trades.append(Trade(
                row        = lot["row"],
                isin       = isin,
                symbol     = lot.get("symbol") or isin,  # use pre-filled ticker if available
                quantity   = net_qty,
                buy_date   = lot["date_str"],
                buy_price  = lot["price"],
                sell_date  = None,
                sell_price = None,
                has_sell   = False,
                trade_type = "buy",
            ))

        summaries.append(FifoSummary(
            isin          = isin,
            total_bought  = total_bought,
            total_sold    = total_sold,
            net_quantity  = max(0.0, round(total_bought - total_sold, 10)),
            realized_gain = round(realized_gain, 2),
        ))

    return FifoResult(
        net_trades = net_trades,
        summaries  = summaries,
        warnings   = warnings,
    )
