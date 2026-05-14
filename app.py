"""Furniture Planner — local Flask app.

Run: python app.py  (then open http://localhost:5000)
"""
from __future__ import annotations

import hmac
import io
import json
import os
from datetime import date

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

import scraper
import shipping
import storage

app = Flask(__name__)

# ---------------------------------------------------------------------------
# HTTP basic auth (one shared password from env)
# ---------------------------------------------------------------------------
AUTH_USER = os.environ.get("BASIC_AUTH_USER", "")
AUTH_PASS = os.environ.get("BASIC_AUTH_PASS", "")
AUTH_ENABLED = bool(AUTH_USER and AUTH_PASS)


def _check_auth(user: str, pw: str) -> bool:
    if not AUTH_ENABLED:
        return True
    return hmac.compare_digest(user, AUTH_USER) and hmac.compare_digest(pw, AUTH_PASS)


@app.before_request
def _require_auth():
    if not AUTH_ENABLED:
        return None
    if request.path.startswith("/static/") or request.path == "/healthz":
        return None
    auth = request.authorization
    if auth and _check_auth(auth.username or "", auth.password or ""):
        return None
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Furniture Planner"'},
    )


@app.route("/healthz")
def healthz():
    return "ok", 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _enrich(item: dict, settings: dict) -> dict:
    """Attach computed arrival window, order-by window, and urgency to an item."""
    wins = shipping.compute_item_windows(item, settings)
    av, ow = wins["arrival"], wins["order"]
    days_left = shipping.days_until_latest_order(ow)
    item = dict(item)
    item.setdefault("needs_assembly", False)
    item.setdefault("assembly_days", 0)
    item["arrival_window"] = av
    item["arrival_label"] = shipping.format_arrival(av)
    item["order_window"] = ow
    item["order_window_label"] = shipping.format_order(ow)
    item["days_left"] = days_left
    item["urgency"] = shipping.urgency_class(days_left)
    return item


def _grouped(items: list[dict], settings: dict) -> dict:
    """Group items by list_type then by category."""
    out = {lt: {} for lt in storage.LIST_TYPES}
    for it in items:
        e = _enrich(it, settings)
        lt = e.get("list_type", "to_buy")
        cat = e.get("category") or "Uncategorized"
        out.setdefault(lt, {}).setdefault(cat, []).append(e)
    # sort items inside each category by urgency (least-days first)
    for lt in out:
        for cat in out[lt]:
            out[lt][cat].sort(
                key=lambda x: (x["days_left"] if x["days_left"] is not None else 9999)
            )
    return out


def _totals(grouped: dict) -> dict:
    """Compute price totals per (list_type, category) and per list_type."""
    out = {}
    for lt, cats in grouped.items():
        per_cat = {}
        lt_total = 0.0
        lt_count = 0
        for cat, items in cats.items():
            cat_total = sum((it.get("price") or 0) for it in items)
            per_cat[cat] = {"count": len(items), "total": cat_total}
            lt_total += cat_total
            lt_count += len(items)
        out[lt] = {"per_category": per_cat, "total": lt_total, "count": lt_count}
    return out


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    settings = storage.get_settings()
    items = storage.list_items()
    grouped = _grouped(items, settings)
    totals = _totals(grouped)
    counts = {lt: totals[lt]["count"] for lt in storage.LIST_TYPES}
    return render_template(
        "index.html",
        settings=settings,
        grouped=grouped,
        counts=counts,
        totals=totals,
        list_types=storage.LIST_TYPES,
        today=date.today().isoformat(),
    )


def _pnl_data(items: list[dict], settings: dict) -> dict:
    """Aggregate spend in many useful slices for the P&L view."""
    enriched = [_enrich(it, settings) for it in items]

    list_types = list(storage.LIST_TYPES)
    settings_cats = list(settings.get("categories") or [])
    cats_in_use = []
    for it in enriched:
        c = it.get("category") or "Uncategorized"
        if c not in cats_in_use:
            cats_in_use.append(c)
    # Preserve settings order, append any custom-typed ones at the end
    categories = [c for c in settings_cats if c in cats_in_use] + [
        c for c in cats_in_use if c not in settings_cats
    ]

    # Category × list-type matrix
    matrix = {c: {lt: {"count": 0, "total": 0.0, "no_price": 0} for lt in list_types} for c in categories}
    for it in enriched:
        c = it.get("category") or "Uncategorized"
        lt = it.get("list_type") or "to_buy"
        cell = matrix.setdefault(c, {lt2: {"count": 0, "total": 0.0, "no_price": 0} for lt2 in list_types}).setdefault(
            lt, {"count": 0, "total": 0.0, "no_price": 0}
        )
        cell["count"] += 1
        p = it.get("price")
        if isinstance(p, (int, float)):
            cell["total"] += p
        else:
            cell["no_price"] += 1

    # Row + column totals
    row_totals = {}
    for c in matrix:
        row = matrix[c]
        row_totals[c] = {
            "count": sum(row[lt]["count"] for lt in list_types),
            "total": sum(row[lt]["total"] for lt in list_types),
            "no_price": sum(row[lt]["no_price"] for lt in list_types),
        }
    col_totals = {}
    for lt in list_types:
        col_totals[lt] = {
            "count": sum(matrix[c][lt]["count"] for c in matrix),
            "total": sum(matrix[c][lt]["total"] for c in matrix),
            "no_price": sum(matrix[c][lt]["no_price"] for c in matrix),
        }
    grand_total = {
        "count": sum(row_totals[c]["count"] for c in row_totals),
        "total": sum(row_totals[c]["total"] for c in row_totals),
        "no_price": sum(row_totals[c]["no_price"] for c in row_totals),
    }

    # Per-store
    by_store: dict[str, dict] = {}
    for it in enriched:
        s = it.get("store") or "generic"
        bucket = by_store.setdefault(s, {"count": 0, "total": 0.0, "no_price": 0})
        bucket["count"] += 1
        p = it.get("price")
        if isinstance(p, (int, float)):
            bucket["total"] += p
        else:
            bucket["no_price"] += 1
    by_store_sorted = sorted(by_store.items(), key=lambda kv: kv[1]["total"], reverse=True)

    # Itemized detail, grouped by category, sorted by list_type then price desc
    list_rank = {lt: i for i, lt in enumerate(list_types)}
    details: dict[str, list[dict]] = {c: [] for c in categories}
    for it in enriched:
        c = it.get("category") or "Uncategorized"
        details.setdefault(c, []).append(it)
    for c in details:
        details[c].sort(key=lambda x: (list_rank.get(x.get("list_type"), 99), -(x.get("price") or 0)))

    return {
        "categories": categories,
        "list_types": list_types,
        "matrix": matrix,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand_total": grand_total,
        "by_store": by_store_sorted,
        "details": details,
    }


@app.route("/pnl")
def pnl_page():
    settings = storage.get_settings()
    items = storage.list_items()
    data = _pnl_data(items, settings)
    return render_template(
        "pnl.html",
        settings=settings,
        data=data,
        list_types=storage.LIST_TYPES,
        list_labels={"to_buy": "To Buy", "wish_list": "Wish List", "interested": "Interested"},
    )


@app.route("/calendar")
def calendar_page():
    settings = storage.get_settings()
    items = [_enrich(it, settings) for it in storage.list_items()]
    today = date.today()

    # Group items by their order_by_latest date. Items in Wish List /
    # Interested are still surfaced so you can see when you'd need to commit
    # if you decide to buy them.
    by_date: dict[str, list[dict]] = {}
    no_date: list[dict] = []
    for it in items:
        ow = it.get("order_window") or {}
        if ow.get("valid") and ow.get("order_latest"):
            by_date.setdefault(ow["order_latest"], []).append(it)
        else:
            no_date.append(it)
    sorted_dates = sorted(by_date.keys())

    # Build a small "next 14 days" strip so the week ahead is scannable.
    from datetime import timedelta as _td
    strip = []
    for i in range(14):
        d = today + _td(days=i)
        ds = d.isoformat()
        strip.append({
            "date": ds,
            "label_day": d.strftime("%a"),
            "label_num": d.strftime("%d"),
            "count": len(by_date.get(ds, [])),
            "is_today": d == today,
        })

    return render_template(
        "calendar.html",
        settings=settings,
        today=today.isoformat(),
        sorted_dates=sorted_dates,
        by_date=by_date,
        no_date=no_date,
        strip=strip,
        list_types=storage.LIST_TYPES,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        updates = {
            "zip_code": (request.form.get("zip_code") or "").strip(),
            "move_in_date": request.form.get("move_in_date") or "2026-06-02",
            "work_start_date": request.form.get("work_start_date") or "2026-06-08",
        }
        new_cat = (request.form.get("new_category") or "").strip()
        storage.update_settings(updates)
        if new_cat:
            storage.add_category(new_cat)
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=storage.get_settings())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.post("/api/scrape")
def api_scrape():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    settings = storage.get_settings()
    data = scraper.scrape(url, zip_code=settings.get("zip_code", ""))

    needs_assembly = bool(payload.get("needs_assembly", False))
    assembly_days = payload.get("assembly_days") or (1 if needs_assembly else 0)
    av = shipping.arrival_window(
        settings["move_in_date"],
        settings["work_start_date"],
        needs_assembly,
        assembly_days,
    )
    ow = shipping.order_by_window(
        av["arrival_earliest"],
        av["arrival_latest"],
        data.get("ship_days_min"),
        data.get("ship_days_max"),
    )
    data["arrival_window"] = av
    data["arrival_label"] = shipping.format_arrival(av)
    data["order_window"] = ow
    data["order_window_label"] = shipping.format_order(ow)
    return jsonify(data)


@app.post("/api/preview-window")
def api_preview_window():
    """Live-recalculate arrival + order windows for the add-item modal."""
    payload = request.get_json(silent=True) or {}
    settings = storage.get_settings()
    av = shipping.arrival_window(
        settings["move_in_date"],
        settings["work_start_date"],
        bool(payload.get("needs_assembly", False)),
        payload.get("assembly_days") or (1 if payload.get("needs_assembly") else 0),
    )
    ow = shipping.order_by_window(
        av["arrival_earliest"],
        av["arrival_latest"],
        payload.get("ship_days_min"),
        payload.get("ship_days_max"),
    )
    return jsonify({
        "arrival_window": av,
        "arrival_label": shipping.format_arrival(av),
        "order_window": ow,
        "order_window_label": shipping.format_order(ow),
    })


@app.post("/api/items")
def api_create_item():
    payload = request.get_json(silent=True) or request.form.to_dict()
    if not payload:
        return jsonify({"error": "missing body"}), 400
    item = storage.create_item(payload)
    return jsonify(_enrich(item, storage.get_settings())), 201


@app.put("/api/items/<item_id>")
def api_update_item(item_id: str):
    payload = request.get_json(silent=True) or {}
    item = storage.update_item(item_id, payload)
    if not item:
        return jsonify({"error": "not found"}), 404
    return jsonify(_enrich(item, storage.get_settings()))


@app.delete("/api/items/<item_id>")
def api_delete_item(item_id: str):
    ok = storage.delete_item(item_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.post("/api/categories")
def api_add_category():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    cats = storage.add_category(name)
    return jsonify({"categories": cats})


@app.get("/backup")
def backup():
    payload = {
        "exported_at": date.today().isoformat(),
        "settings": storage.get_settings(),
        "items": storage.list_items(),
    }
    buf = io.BytesIO(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"furniture-planner-backup-{date.today().isoformat()}.json",
        mimetype="application/json",
    )


@app.post("/restore")
def restore():
    file = request.files.get("backup")
    if not file:
        return "No file uploaded", 400
    try:
        data = json.loads(file.read().decode("utf-8"))
    except Exception as e:
        return f"Could not parse JSON: {e}", 400
    settings = data.get("settings")
    items = data.get("items")
    if not isinstance(settings, dict) or not isinstance(items, list):
        return "Backup file is missing settings/items.", 400
    # Replace the files atomically via storage helpers
    storage._ensure_files()  # noqa: SLF001
    storage._write(storage.SETTINGS_PATH, settings)  # noqa: SLF001
    storage._write(storage.ITEMS_PATH, items)  # noqa: SLF001
    return redirect(url_for("settings_page"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
