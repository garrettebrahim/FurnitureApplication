# Deploying Furniture Planner to Render

Estimated time: **~10 minutes**, including signup. Cost: **$0**.

## What you'll end up with

- A real URL like `https://furniture-planner-xxxx.onrender.com`
- HTTP basic auth (one shared username + password) gating access
- Auto-deploys whenever you push to the GitHub repo
- Backup/Restore buttons in Settings so you can save and re-upload your data
  (Render's free tier has ephemeral storage — see "Data persistence" below)

---

## One-time setup

### 1. Push the code to GitHub

The repo has already been initialized locally. You just need a GitHub remote.

1. Go to <https://github.com/new>, create a **private** repo named whatever
   you like (e.g. `furniture-planner`). Do **not** initialize it with a README.
2. Copy the URL it gives you (looks like
   `https://github.com/<your-username>/furniture-planner.git`).
3. Back in PowerShell, from the project folder:

   ```powershell
   cd "C:\Users\User\Documents\Furniture Program"
   git remote add origin https://github.com/<your-username>/furniture-planner.git
   git push -u origin main
   ```

   GitHub will prompt you to sign in (browser popup or PAT) the first time.

### 2. Create a Render account

1. Go to <https://render.com>, click **Get Started**, sign in with your
   GitHub account so Render can see your repos.

### 3. Deploy via the Blueprint file

The repo already includes a `render.yaml` so Render knows exactly how to
build and run the app.

1. In Render, click **New → Blueprint**.
2. Pick the GitHub repo you just pushed.
3. Render will read `render.yaml` and show you the plan. It will say it
   needs values for two environment variables:
   - `BASIC_AUTH_USER` — set to whatever you want (e.g. `garrett`)
   - `BASIC_AUTH_PASS` — pick a long random password and write it down
4. Click **Apply** / **Deploy**.

The first build takes ~3–5 minutes (it installs Python deps including the
TLS-impersonation library used by the scraper). When it's green, Render
shows your URL at the top of the service page.

### 4. Share

Open the URL in a private window. You'll see a browser auth prompt — enter
the username and password from step 3. Send those to whoever you want to
grant access to.

---

## Data persistence — read this!

Render's **free** plan has ephemeral disk: anything you write to local
files **may be wiped** when:

- You push code (each deploy rebuilds the container)
- The container restarts after a crash
- The free plan spins down after 15 min of inactivity *(usually preserves
  data on wake, but not guaranteed)*

The app has built-in mitigations:

- **Settings → Download backup (JSON)** — grabs a single JSON file with
  every item and your settings.
- **Settings → Upload & restore** — re-imports the backup file.

**Recommended workflow:** after a session of adding items, hit "Download
backup" and save the file. If you ever come back and the list is empty,
upload that file to restore everything.

If losing data ever becomes a real issue, two upgrade paths:

1. **Add a persistent disk** — Render Starter plan ($7/mo) + disk ($1/mo
   for 1 GB). Change the `plan: free` line in `render.yaml` to `starter`
   and uncomment a disk block; details in Render docs.
2. **Move storage to Postgres** — Supabase free tier is generous and
   doesn't expire. Requires code changes in `storage.py`.

---

## Future updates

Anytime you change code locally:

```powershell
cd "C:\Users\User\Documents\Furniture Program"
git add -A
git commit -m "Describe what you changed"
git push
```

Render auto-deploys on push. ~2 minutes from push to live.

---

## Troubleshooting

**Auth prompt loop:** make sure `BASIC_AUTH_USER` and `BASIC_AUTH_PASS`
are set in Render → Service → Environment. If either is empty the app
disables auth (open to anyone).

**Cold start delay (~30s):** free tier sleeps after 15 min idle. First
request wakes it up. Subsequent requests are fast.

**Scraper returns "HTTP 403 bot-blocked":** the bot wall changed its
fingerprint. Re-run the scrape, or paste shipping days manually using
the Ship-days button on the card.

**Lost data after deploy:** the Restore button in Settings accepts the
JSON backup file you downloaded earlier.
