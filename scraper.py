"""Best-effort product scraping for the 5 target stores plus a generic
Open Graph / JSON-LD fallback for everything else.

Notes:
- Many large retailers (Amazon, Target, Wayfair) bot-block single requests.
  When that happens we fall back to manual entry — by design.
- Shipping-day fields throughout the app are CALENDAR days (not business
  days). Business-day windows scraped from pages are converted to calendar
  days using a conservative `ceil(days * 7/5)`.
"""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# curl_cffi provides Chrome TLS-fingerprint impersonation, which is required
# to get past Akamai / PerimeterX bot walls (Anthropologie, Target, Wayfair,
# Amazon all use one of these). Falls back to plain requests if not installed.
try:
    from curl_cffi import requests as _cffi_requests  # type: ignore
    _HAS_CFFI = True
except ImportError:  # pragma: no cover
    _cffi_requests = None
    _HAS_CFFI = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}
TIMEOUT = 20

# Per-store CALENDAR-day shipping defaults. These reflect the typical
# experience for each store today and are used only when we cannot read a
# better signal off the actual product page.
STORE_SHIP_DEFAULTS = {
    # Furniture-realistic defaults (calendar days). The user is shopping for
    # a move, so these lean toward bulky/heavier items rather than Prime
    # small-parcel speeds. Override per item once you verify on the page.
    "amazon": (3, 8),         # Prime small items 2-4d; furniture 5-14d
    "wayfair": (7, 14),       # in-stock items; oversize freight 10-21d
    "quince": (14, 21),       # items fulfilled from overseas
    "anthropologie": (7, 14), # furniture 7-14d, apparel 3-7d
    "target": (4, 10),        # small items 3-5d; furniture 5-12d
    "generic": (5, 14),
}

# Items whose URL or name contains these tokens get bumped up — typical for
# heavy/large items that ship freight rather than parcel.
FURNITURE_TOKENS = (
    "sofa", "couch", "sectional", "loveseat", "armchair", "recliner",
    "bed-frame", "bedframe", "headboard", "mattress", "dresser", "wardrobe",
    "nightstand", "armoire", "desk", "bookcase", "bookshelf", "shelf-unit",
    "dining-table", "coffee-table", "console-table", "side-table", "credenza",
    "buffet", "sideboard", "ottoman", "bench",
)

QUINCE_MTO_BUSINESS_DAYS = (15, 20)  # advertised window for Made-to-Order

# Stores that sit behind Akamai/PerimeterX. We use curl_cffi + a homepage
# warm-up to seed cookies for these. Falls back to plain requests otherwise.
BOT_PROTECTED_STORES = {"anthropologie", "target", "wayfair", "amazon"}

# Cached per-host curl_cffi sessions so warm-up cookies persist across scrapes.
_SESSIONS: dict[str, "object"] = {}
_SESSION_LOCK = threading.Lock()


def _store_home(store: str) -> Optional[str]:
    return {
        "anthropologie": "https://www.anthropologie.com/",
        "target": "https://www.target.com/",
        "wayfair": "https://www.wayfair.com/",
        "amazon": "https://www.amazon.com/",
        "quince": "https://www.quince.com/",
    }.get(store)


def _get_session(store: str):
    """Return a warmed cffi session for a store, or None if cffi unavailable."""
    if not _HAS_CFFI:
        return None
    with _SESSION_LOCK:
        s = _SESSIONS.get(store)
        if s is not None:
            return s
        s = _cffi_requests.Session(impersonate="chrome124")
        home = _store_home(store)
        if home:
            try:
                s.get(home, timeout=15)
            except Exception:
                # Warm-up failed (rare); we'll still try the product fetch.
                pass
        _SESSIONS[store] = s
        return s


def _fetch(url: str, store: str):
    """Single-call HTTP fetch. For bot-protected stores uses curl_cffi
    Chrome impersonation + a session-warmed cookie jar; otherwise plain
    requests. Returns a response-like object with .status_code and .text."""
    if store in BOT_PROTECTED_STORES and _HAS_CFFI:
        s = _get_session(store)
        if s is not None:
            headers = {"Referer": _store_home(store) or "https://www.google.com/"}
            return s.get(url, timeout=TIMEOUT, headers=headers)
    return requests.get(url, headers=REQUEST_HEADERS, timeout=TIMEOUT)


def biz_to_cal(days: int) -> int:
    """Conservatively convert business days -> calendar days."""
    return math.ceil(days * 7 / 5)


@dataclass
class ScrapeResult:
    url: str
    store: str = "generic"
    name: Optional[str] = None
    price: Optional[float] = None
    price_text: Optional[str] = None
    image_url: Optional[str] = None
    ship_days_min: Optional[int] = None
    ship_days_max: Optional[int] = None
    ship_source: str = "default"  # "page" | "default" | "manual"
    ship_source_detail: str = ""
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


def detect_store(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "amazon." in host:
        return "amazon"
    if "wayfair." in host:
        return "wayfair"
    if "quince." in host:
        return "quince"
    if "anthropologie." in host:
        return "anthropologie"
    if "target." in host:
        return "target"
    return "generic"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d{1,3}(?:[,\d]{0,})(?:\.\d{1,2})?)", text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _meta(soup: BeautifulSoup, prop: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            yield from (d for d in data if d is not None)
        else:
            yield data


def _extract_from_jsonld(soup: BeautifulSoup) -> dict:
    out: dict = {}
    for block in _iter_jsonld(soup):
        if not isinstance(block, dict):
            continue
        graph = block.get("@graph")
        candidates = graph if isinstance(graph, list) else [block]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type")
            types = t if isinstance(t, list) else [t]
            if not any(isinstance(x, str) and x.lower() == "product" for x in types):
                continue
            if not out.get("name") and c.get("name"):
                out["name"] = str(c["name"]).strip()
            img = c.get("image")
            if not out.get("image") and img:
                if isinstance(img, list) and img:
                    out["image"] = img[0] if isinstance(img[0], str) else None
                elif isinstance(img, str):
                    out["image"] = img
                elif isinstance(img, dict) and img.get("url"):
                    out["image"] = img["url"]
            offers = c.get("offers")
            if offers and out.get("price") is None:
                off_list = offers if isinstance(offers, list) else [offers]
                for off in off_list:
                    if not isinstance(off, dict):
                        continue
                    p = off.get("price") or off.get("lowPrice")
                    if p is not None:
                        try:
                            out["price"] = float(p)
                        except (TypeError, ValueError):
                            pass
                    # Some sites publish OfferShippingDetails with deliveryTime
                    sd = off.get("shippingDetails")
                    if sd and not out.get("shipping_min"):
                        ship = _shipping_from_offer_details(sd)
                        if ship:
                            out["shipping_min"], out["shipping_max"] = ship
    return out


def _shipping_from_offer_details(sd) -> Optional[tuple[int, int]]:
    """Pull a calendar-day range out of an OfferShippingDetails dict."""
    blocks = sd if isinstance(sd, list) else [sd]
    for s in blocks:
        if not isinstance(s, dict):
            continue
        dt = s.get("deliveryTime")
        if not isinstance(dt, dict):
            continue
        handling = dt.get("handlingTime") or {}
        transit = dt.get("transitTime") or {}
        h_min = (handling.get("minValue") if isinstance(handling, dict) else 0) or 0
        h_max = (handling.get("maxValue") if isinstance(handling, dict) else 0) or 0
        t_min = (transit.get("minValue") if isinstance(transit, dict) else 0) or 0
        t_max = (transit.get("maxValue") if isinstance(transit, dict) else 0) or 0
        unit = (
            (handling.get("unitCode") if isinstance(handling, dict) else None)
            or (transit.get("unitCode") if isinstance(transit, dict) else None)
            or "DAY"
        )
        total_min = int(h_min) + int(t_min)
        total_max = int(h_max) + int(t_max)
        if total_max <= 0:
            continue
        # unitCode "DAY" is calendar days; "d" is also days
        return total_min, total_max
    return None


def _generic_extract(soup: BeautifulSoup, result: ScrapeResult) -> None:
    """Populate name/image/price + (optionally) shipping range from generic
    JSON-LD / OG / DOM signals."""
    jld = _extract_from_jsonld(soup)
    if jld.get("name"):
        result.name = jld["name"]
    if jld.get("image"):
        result.image_url = jld["image"]
    if jld.get("price") is not None:
        result.price = jld["price"]
        result.price_text = f"${jld['price']:.2f}"
    if jld.get("shipping_min") and jld.get("shipping_max"):
        result.ship_days_min = jld["shipping_min"]
        result.ship_days_max = jld["shipping_max"]
        result.ship_source = "page"
        result.ship_source_detail = "Schema.org OfferShippingDetails"

    if not result.name:
        result.name = _meta(soup, "og:title") or (
            soup.title.string.strip() if soup.title and soup.title.string else None
        )
    if not result.image_url:
        result.image_url = _meta(soup, "og:image")
    if result.price is None:
        og_price = _meta(soup, "product:price:amount") or _meta(soup, "og:price:amount")
        if og_price:
            result.price = _parse_price(og_price)
            if result.price is not None:
                result.price_text = f"${result.price:.2f}"
    if result.price is None:
        for sel in [
            '[itemprop="price"]',
            '[data-testid*="price"]',
            ".price",
            ".product-price",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".a-price .a-offscreen",
        ]:
            for tag in soup.select(sel):
                p = _parse_price(tag.get_text(" ", strip=True))
                if p is not None:
                    result.price = p
                    result.price_text = f"${p:.2f}"
                    break
            if result.price is not None:
                break


def _detect_ship_days_from_copy(soup: BeautifulSoup) -> Optional[tuple[int, int, str]]:
    """Last-resort regex over visible page text. Returns (min,max,note)."""
    text = soup.get_text(" ", strip=True)[:120_000]
    low = text.lower()
    range_patterns = [
        (
            r"(?:ships?|delivers?|delivery|arrives?)\s+(?:in\s+)?(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(business)?\s*days",
            True,
        ),
        (
            r"(?:estimated|expected)\s+(?:delivery|arrival)\s*(?:of\s+)?(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(business)?\s*days",
            True,
        ),
        (
            r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(business)?\s*day\s+(?:shipping|delivery)",
            True,
        ),
    ]
    for pat, has_biz in range_patterns:
        m = re.search(pat, low)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            biz = bool(has_biz and m.group(3))
            if biz:
                return biz_to_cal(lo), biz_to_cal(hi), "page (business→calendar)"
            return lo, hi, "page"
    return None


# ---------------------------------------------------------------------------
# Per-store handlers
# ---------------------------------------------------------------------------
def _next_data(soup: BeautifulSoup):
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return None
    try:
        return json.loads(tag.string or tag.get_text() or "{}")
    except json.JSONDecodeError:
        return None


def _extract_quince(soup: BeautifulSoup, result: ScrapeResult) -> None:
    """Quince ships most items overseas → defaults are slow. Use __NEXT_DATA__
    to pull name/image/price and detect Made-to-Order."""
    nd = _next_data(soup)
    if not nd:
        return
    try:
        product = nd["props"]["pageProps"]["pageData"]["context"]["pageDataJson"]["product"]
    except (KeyError, TypeError):
        return

    if not result.name and product.get("title"):
        result.name = product["title"]
    if not result.image_url:
        images = product.get("images") or []
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                u = first.get("url") or first.get("src")
                if u:
                    result.image_url = u if u.startswith("http") else "https:" + u
            elif isinstance(first, str):
                result.image_url = first if first.startswith("http") else "https:" + first

    # Variant-aware price (pick the cheapest in-stock variant as a safe display)
    if result.price is None:
        prices = []
        for v in product.get("variants") or []:
            p = v.get("price")
            if isinstance(p, (int, float)):
                prices.append(float(p))
            elif isinstance(p, dict):
                pv = p.get("amount") or p.get("value")
                if isinstance(pv, (int, float)):
                    prices.append(float(pv))
        if prices:
            result.price = min(prices)
            result.price_text = f"${result.price:.2f}"

    # Shipping
    is_mto = bool(product.get("isMadeToOrder")) or (
        product.get("madeToOrderText") not in (None, "")
    )
    if is_mto:
        lo, hi = QUINCE_MTO_BUSINESS_DAYS
        result.ship_days_min = biz_to_cal(lo)
        result.ship_days_max = biz_to_cal(hi)
        result.ship_source = "page"
        result.ship_source_detail = (
            f"Made-to-Order: {lo}-{hi} business days (≈{biz_to_cal(lo)}-{biz_to_cal(hi)} calendar)"
        )
    else:
        # Check the description for "Crafted in [overseas country]" — those
        # items still ship from abroad even when in-stock.
        description = (product.get("description") or "") + " " + (product.get("details") or "")
        overseas_countries = (
            "india",
            "cambodia",
            "china",
            "italy",
            "portugal",
            "turkey",
            "vietnam",
            "indonesia",
            "peru",
            "mongolia",
        )
        text_low = re.sub(r"<[^>]+>", " ", description).lower()
        m = re.search(r"crafted in ([a-z, ]+?)(?:[<.\n]|$)", text_low)
        if m and any(c in m.group(1) for c in overseas_countries):
            result.ship_days_min, result.ship_days_max = STORE_SHIP_DEFAULTS["quince"]
            result.ship_source = "default"
            result.ship_source_detail = (
                f"Quince standard delivery for overseas-crafted items (~14-21 calendar days, '{m.group(1).strip()}')"
            )
        else:
            result.ship_days_min, result.ship_days_max = STORE_SHIP_DEFAULTS["quince"]
            result.ship_source = "default"
            result.ship_source_detail = "Quince standard delivery (~14-21 calendar days)"


def _extract_anthropologie(soup: BeautifulSoup, result: ScrapeResult) -> None:
    """Anthropologie publishes a clean Product JSON-LD plus a shipping table
    with literal copy like 'Standard 4-8 business days'. We rely on the
    generic JSON-LD reader for product fields and parse the table for the
    shipping window."""
    text = soup.get_text(" ", strip=True)

    # Standard shipping line: "Standard 4-8 business days"
    m = re.search(
        r"standard\s+(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*business\s*days",
        text,
        re.I,
    )
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        result.ship_days_min = biz_to_cal(lo)
        result.ship_days_max = biz_to_cal(hi)
        result.ship_source = "page"
        result.ship_source_detail = (
            f"Anthropologie Standard shipping: {lo}-{hi} business days "
            f"(≈{biz_to_cal(lo)}-{biz_to_cal(hi)} calendar)"
        )
        return

    # Some Anthro furniture items show "Ships in X-Y weeks" or "White glove"
    m = re.search(r"ships?\s+in\s+(\d{1,2})\s*[-–to]+\s*(\d{1,2})\s*weeks", text, re.I)
    if m:
        lo, hi = int(m.group(1)) * 7, int(m.group(2)) * 7
        result.ship_days_min = lo
        result.ship_days_max = hi
        result.ship_source = "page"
        result.ship_source_detail = (
            f"Anthropologie freight shipping: {m.group(1)}-{m.group(2)} weeks (≈{lo}-{hi} calendar days)"
        )
        return

    if re.search(r"white\s*glove\s*delivery", text, re.I):
        result.ship_days_min = 14
        result.ship_days_max = 28
        result.ship_source = "page"
        result.ship_source_detail = "Anthropologie white-glove delivery (typically 2-4 weeks)"


def _extract_wayfair(soup: BeautifulSoup, result: ScrapeResult) -> None:
    """Wayfair publishes shipping windows in JSON-LD `OfferShippingDetails`
    with handling+transit times — already handled in the generic JSON-LD
    extractor. This hook stays for future Wayfair-specific parsing."""
    return


def _generic_nextdata_probe(soup: BeautifulSoup, result: ScrapeResult) -> None:
    """For Next.js sites (Target, Anthropologie often, others) walk
    __NEXT_DATA__ looking for plausible product fields when generic
    extraction missed them. Conservative — only fills gaps."""
    nd = _next_data(soup)
    if not nd:
        return

    def first_match(o, keys, max_depth=10, depth=0):
        if depth > max_depth:
            return None
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if any(t == kl for t in keys) and isinstance(v, (str, int, float)):
                    return v
                hit = first_match(v, keys, max_depth, depth + 1)
                if hit is not None:
                    return hit
        elif isinstance(o, list):
            for x in o[:25]:
                hit = first_match(x, keys, max_depth, depth + 1)
                if hit is not None:
                    return hit
        return None

    if not result.name:
        name = first_match(nd, {"name", "title", "displayname", "productname"})
        if isinstance(name, str) and name.strip():
            result.name = name.strip()
    if result.price is None:
        price = first_match(nd, {"price", "currentprice", "saleprice", "listprice"})
        if isinstance(price, (int, float)):
            result.price = float(price)
            result.price_text = f"${result.price:.2f}"
        elif isinstance(price, str):
            p = _parse_price(price)
            if p is not None:
                result.price = p
                result.price_text = f"${p:.2f}"
    if not result.image_url:
        img = first_match(nd, {"primaryimage", "imageurl", "image", "primary_image_url"})
        if isinstance(img, str) and img.startswith(("http", "//")):
            result.image_url = img if img.startswith("http") else "https:" + img


def _looks_like_furniture(url: str, name: Optional[str]) -> bool:
    blob = (url + " " + (name or "")).lower()
    return any(tok in blob for tok in FURNITURE_TOKENS)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def scrape(url: str, zip_code: str = "") -> dict:
    """Fetch the URL and extract product details. Always returns a dict
    (never raises) — check the `ok` field and `warnings` list."""
    result = ScrapeResult(url=url, store=detect_store(url))
    if not url or not url.startswith(("http://", "https://")):
        result.ok = False
        result.warnings.append("URL must start with http:// or https://")
        return asdict(result)

    try:
        resp = _fetch(url, result.store)
    except Exception as e:
        result.ok = False
        result.warnings.append(f"Could not fetch page: {e.__class__.__name__}")
        smin, smax = STORE_SHIP_DEFAULTS.get(result.store, STORE_SHIP_DEFAULTS["generic"])
        result.ship_days_min, result.ship_days_max, result.ship_source = smin, smax, "default"
        result.ship_source_detail = f"{result.store} default (page unreachable)"
        return asdict(result)

    if resp.status_code >= 400:
        result.ok = False
        result.warnings.append(
            f"Site returned HTTP {resp.status_code} (often means bot-blocked — enter details manually)"
        )
        smin, smax = STORE_SHIP_DEFAULTS.get(result.store, STORE_SHIP_DEFAULTS["generic"])
        result.ship_days_min, result.ship_days_max, result.ship_source = smin, smax, "default"
        result.ship_source_detail = f"{result.store} default (bot-blocked)"
        return asdict(result)

    soup = BeautifulSoup(resp.text, "lxml")
    _generic_extract(soup, result)

    # Store-specific enrichment (may overwrite ship_days set by generic)
    if result.store == "quince":
        _extract_quince(soup, result)
    elif result.store == "wayfair":
        _extract_wayfair(soup, result)
    elif result.store == "anthropologie":
        _extract_anthropologie(soup, result)

    # For Next.js sites (Target/Anthropologie/etc.), backfill missing fields
    # from __NEXT_DATA__ when generic JSON-LD / OG missed them.
    if result.store in {"target", "anthropologie"} and (
        not result.name or result.price is None or not result.image_url
    ):
        _generic_nextdata_probe(soup, result)

    # If still no shipping info, try regex over visible copy.
    if result.ship_days_min is None or result.ship_days_max is None:
        detected = _detect_ship_days_from_copy(soup)
        if detected:
            result.ship_days_min, result.ship_days_max, src = detected
            result.ship_source = "page"
            result.ship_source_detail = f"text scan ({src})"
        else:
            smin, smax = STORE_SHIP_DEFAULTS.get(result.store, STORE_SHIP_DEFAULTS["generic"])
            # Bump furniture-likely items by a couple days — bulky things ship
            # freight, not parcel, and the user is shopping for a move.
            if _looks_like_furniture(url, result.name) and result.store != "quince":
                smin = max(smin, 7)
                smax = max(smax, smax + 3)
                result.ship_source_detail = (
                    f"{result.store} furniture default (looks-like-furniture URL/name)"
                )
            else:
                result.ship_source_detail = f"{result.store} default"
            result.ship_days_min, result.ship_days_max = smin, smax
            result.ship_source = "default"

    if not result.name:
        result.warnings.append("Could not detect product name — please edit manually")
    if result.price is None:
        result.warnings.append("Could not detect price — please enter manually")
    if result.ship_source == "default":
        result.warnings.append(
            f"Shipping estimate is a default ({result.ship_source_detail}). "
            "Verify on the product page with your zip code and adjust below if needed."
        )
    if zip_code:
        # NOTE: we don't POST the zip back to the site — each retailer has a
        # different mechanism and most block automated POSTs. The user can
        # verify and override manually.
        pass

    return asdict(result)
