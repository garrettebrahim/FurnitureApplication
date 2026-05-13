"""JSON-backed persistence for items and settings."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
)
ITEMS_PATH = os.path.join(DATA_DIR, "items.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

LIST_TYPES = ("to_buy", "wish_list", "interested")
DEFAULT_CATEGORIES = ["Living Room", "Bedroom", "Kitchen"]

_lock = threading.Lock()


DEFAULT_SETTINGS = {
    "zip_code": "",
    "categories": DEFAULT_CATEGORIES,
    "move_in_date": "2026-06-02",
    "work_start_date": "2026-06-08",
}


def _ensure_files() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(ITEMS_PATH):
        with open(ITEMS_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)


def _migrate_settings(s: dict) -> dict:
    """Bring an older settings.json up to the current schema."""
    changed = False
    if "move_in_date" not in s:
        # Old key was arrival_start
        s["move_in_date"] = s.pop("arrival_start", DEFAULT_SETTINGS["move_in_date"])
        changed = True
    if "work_start_date" not in s:
        # Old key was arrival_end; if it was Jun 6 keep Jun 8 default,
        # otherwise carry the value forward as the cutoff.
        old_end = s.pop("arrival_end", None)
        s["work_start_date"] = (
            old_end if old_end and old_end >= "2026-06-07" else DEFAULT_SETTINGS["work_start_date"]
        )
        changed = True
    # Drop any leftover legacy keys
    for legacy in ("arrival_start", "arrival_end"):
        if legacy in s:
            s.pop(legacy, None)
            changed = True
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    if changed:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    return s


def _read(path: str) -> Any:
    _ensure_files()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data: Any) -> None:
    _ensure_files()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# Settings
def get_settings() -> dict:
    return _migrate_settings(_read(SETTINGS_PATH))


def update_settings(updates: dict) -> dict:
    with _lock:
        s = _read(SETTINGS_PATH)
        s.update({k: v for k, v in updates.items() if v is not None})
        _write(SETTINGS_PATH, s)
        return s


def add_category(name: str) -> list[str]:
    name = name.strip()
    if not name:
        return get_settings().get("categories", [])
    with _lock:
        s = _read(SETTINGS_PATH)
        cats = s.setdefault("categories", list(DEFAULT_CATEGORIES))
        if name not in cats:
            cats.append(name)
        _write(SETTINGS_PATH, s)
        return cats


# Items
def list_items() -> list[dict]:
    return _read(ITEMS_PATH)


def get_item(item_id: str) -> dict | None:
    for it in list_items():
        if it["id"] == item_id:
            return it
    return None


def create_item(data: dict) -> dict:
    item = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "name": (data.get("name") or "").strip() or "Untitled item",
        "price": data.get("price"),
        "image_url": data.get("image_url"),
        "source_url": data.get("source_url", ""),
        "store": data.get("store"),
        "category": data.get("category") or "Living Room",
        "list_type": data.get("list_type") or "to_buy",
        "ship_days_min": data.get("ship_days_min"),
        "ship_days_max": data.get("ship_days_max"),
        "ship_source": data.get("ship_source", "manual"),
        "needs_assembly": bool(data.get("needs_assembly", False)),
        "assembly_days": int(data.get("assembly_days") or 0),
        "notes": data.get("notes", ""),
    }
    if item["needs_assembly"] and item["assembly_days"] < 1:
        item["assembly_days"] = 1
    if item["list_type"] not in LIST_TYPES:
        item["list_type"] = "to_buy"
    with _lock:
        items = _read(ITEMS_PATH)
        items.append(item)
        _write(ITEMS_PATH, items)
    return item


def update_item(item_id: str, updates: dict) -> dict | None:
    with _lock:
        items = _read(ITEMS_PATH)
        for i, it in enumerate(items):
            if it["id"] == item_id:
                for k, v in updates.items():
                    if k in ("id", "created_at"):
                        continue
                    it[k] = v
                items[i] = it
                _write(ITEMS_PATH, items)
                return it
    return None


def delete_item(item_id: str) -> bool:
    with _lock:
        items = _read(ITEMS_PATH)
        new = [it for it in items if it["id"] != item_id]
        if len(new) == len(items):
            return False
        _write(ITEMS_PATH, new)
        return True
