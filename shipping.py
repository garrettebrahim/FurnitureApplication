"""Back-calculate per-item order-by date range.

Model:
  - `move_in_date`: earliest acceptable arrival (can't receive before in unit)
  - `work_start_date`: hard cutoff — everything must be on hand before this date
  - Per-item flag `needs_assembly` plus `assembly_days` shrinks the cutoff:
      assembly items must arrive by  work_start - assembly_days
      non-assembly items must arrive by  work_start - 1
"""
from __future__ import annotations

from datetime import date, datetime, timedelta


def _parse(d: str | date) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(d, "%Y-%m-%d").date()


def arrival_window(
    move_in_date: str | date,
    work_start_date: str | date,
    needs_assembly: bool,
    assembly_days: int | None,
) -> dict:
    """Compute [earliest, latest] arrival dates for one item."""
    move_in = _parse(move_in_date)
    work_start = _parse(work_start_date)
    if needs_assembly:
        days = max(1, int(assembly_days or 1))
        latest = work_start - timedelta(days=days)
    else:
        latest = work_start - timedelta(days=1)
    return {
        "arrival_earliest": move_in.isoformat(),
        "arrival_latest": latest.isoformat(),
        "valid": move_in <= latest,
    }


def order_by_window(
    arrival_earliest: str | date,
    arrival_latest: str | date,
    ship_days_min: int | None,
    ship_days_max: int | None,
) -> dict:
    """Given an arrival window and a ship-day range, return the order-by window.

    arrival = order_date + ship_days
    order_date >= arrival_earliest - ship_days_max
    order_date <= arrival_latest  - ship_days_min
    """
    earliest = _parse(arrival_earliest)
    latest = _parse(arrival_latest)
    if ship_days_min is None or ship_days_max is None:
        return {
            "order_earliest": None,
            "order_latest": None,
            "valid": False,
            "reason": "ship_days unknown",
        }
    try:
        smin = int(ship_days_min)
        smax = int(ship_days_max)
    except (TypeError, ValueError):
        return {"order_earliest": None, "order_latest": None, "valid": False, "reason": "ship_days not numeric"}
    if smin < 0 or smax < 0 or smax < smin:
        return {"order_earliest": None, "order_latest": None, "valid": False, "reason": "invalid ship_days"}
    o_earliest = earliest - timedelta(days=smax)
    o_latest = latest - timedelta(days=smin)
    return {
        "order_earliest": o_earliest.isoformat(),
        "order_latest": o_latest.isoformat(),
        "valid": o_earliest <= o_latest,
        "reason": "" if o_earliest <= o_latest else "window empty (ship times too long for arrival window)",
    }


def compute_item_windows(item: dict, settings: dict) -> dict:
    """Convenience: arrival + order windows in one call."""
    av = arrival_window(
        settings["move_in_date"],
        settings["work_start_date"],
        item.get("needs_assembly", False),
        item.get("assembly_days"),
    )
    ow = order_by_window(
        av["arrival_earliest"],
        av["arrival_latest"],
        item.get("ship_days_min"),
        item.get("ship_days_max"),
    )
    return {"arrival": av, "order": ow}


def format_arrival(av: dict) -> str:
    a = _parse(av["arrival_earliest"])
    b = _parse(av["arrival_latest"])
    if a == b:
        return f"Arrive by {a.strftime('%b %d')}"
    if a.month == b.month:
        return f"Arrive {a.strftime('%b %d')}–{b.day}"
    return f"Arrive {a.strftime('%b %d')} – {b.strftime('%b %d')}"


def format_order(ow: dict) -> str:
    if not ow.get("valid"):
        if ow.get("reason") and ow["reason"] != "ship_days unknown":
            return f"Cannot order in time — {ow['reason']}"
        return "Set shipping days to calculate"
    a = _parse(ow["order_earliest"])
    b = _parse(ow["order_latest"])
    if a == b:
        return f"Order by {a.strftime('%b %d')}"
    if a.month == b.month:
        return f"Order {a.strftime('%b %d')}–{b.day}"
    return f"Order {a.strftime('%b %d')} – {b.strftime('%b %d')}"


def days_until_latest_order(ow: dict, today: date | None = None) -> int | None:
    if not ow.get("valid"):
        return None
    today = today or date.today()
    return (_parse(ow["order_latest"]) - today).days


def urgency_class(days_left: int | None) -> str:
    if days_left is None:
        return "unknown"
    if days_left < 0:
        return "overdue"
    if days_left <= 3:
        return "urgent"
    if days_left <= 10:
        return "soon"
    return "ok"
