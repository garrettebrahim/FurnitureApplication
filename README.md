# Furniture Planner

Local Flask app to plan furniture purchases so they arrive **June 2–6, 2026**.

## Quick start

```powershell
cd "C:\Users\User\Documents\Furniture Program"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000 in your browser.

## How it works

1. Set your destination zip code in **Settings**.
2. Click **+ Add Item**, paste a product URL (Amazon, Wayfair, Quince, Anthropologie, Target, or any site with Open Graph tags).
3. The app scrapes the product name, price, and image and tries to detect shipping days. If detection fails, enter min/max shipping days manually.
4. The app back-calculates the **order-by date range** to hit the Jun 2–6 arrival window.
5. Organize items by **category** (Living Room, Bedroom, Kitchen, +your own) and **list status** (To Buy / Wish List / Interested).

Data is stored in `data/items.json` and `data/settings.json`.
