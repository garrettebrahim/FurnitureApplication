# Furniture Planner

Local-first Flask app to plan furniture purchases so everything arrives between **move-in (Jun 2)** and **work start (Jun 8)**, accounting for per-item assembly time.

Features:
- Three lists (To Buy / Wish List / Interested) × categories (Living Room, Bedroom, …)
- URL scraper with TLS-fingerprint bypass for Anthropologie, Target, Wayfair, Amazon (in addition to Quince's `__NEXT_DATA__` parser)
- Per-item ship-day estimate with **"verified"** flag once you confirm on the product page
- Calendar / agenda view of order-by dates
- P&L tab — spend matrix by category × list, by-store breakdown, itemized subtotals
- Backup / restore as JSON

## Run locally

```powershell
cd "C:\Users\User\Documents\Furniture Program"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. No auth locally (set the env vars below to enable it).

## Optional environment variables

| Var | What it does |
|---|---|
| `BASIC_AUTH_USER` | If set together with `BASIC_AUTH_PASS`, enables HTTP basic auth on every route. |
| `BASIC_AUTH_PASS` | Password for basic auth. |
| `DATA_DIR` | Override where `items.json` / `settings.json` live (defaults to `./data`). Set this when deploying so it points at a writable directory. |

## Deploy to a shared URL

See [`DEPLOY.md`](./DEPLOY.md) — step-by-step Render.com walkthrough, free tier, with auth.
