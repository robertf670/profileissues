# Profile Issues — Dublin Bus schedule auditor

A small **Streamlit** app for Dublin Bus drivers and anyone auditing **published timetables**. For a chosen trip it computes, from **NTA GTFS** data:

- Stop-to-stop **distance along the official route shape** (not straight-line)
- **Timetabled** time per segment
- **Implied speed** (km/h) for each leg — useful for spotting unrealistic slack or tight schedules

**Repository:** [github.com/robertf670/profileissues](https://github.com/robertf670/profileissues)

---

## Features

- Download **GTFS Dublin Bus** into `data/current/` (refreshed from the TFI/NTA feed when you use **Download / refresh GTFS**)
- Match a trip by **route**, **inbound/outbound**, **first departure from terminus** (typed time), and **service date**
- Full **segment table** with timetable times, **M:SS** segment duration, map, CSV & Excel export
- Trip summary and URL state so you can bookmark or refresh with the same query (see app behaviour)

See **[CHANGELOG.md](CHANGELOG.md)** for version history and how we track changes.

### Route scan

In the **sidebar**, **Route scan** runs the same segment/flag logic on **every trip** for a chosen **route**, **service date**, and **direction(s)** (default: outbound and inbound). Results open in **Route scan results** on the main page: trips with **any flagged segment** or a **build error**. Large routes are capped at **500** trips per run (can be slow on Streamlit Cloud for busy routes).

Set **`HIDE_ROUTE_SCAN=1`** in `.env` or **Streamlit secrets** if you want to hide this block (e.g. shared public deployment).

After a scan, use **Download route scan (CSV)** or **(Excel)** in the results expander for the same table plus summary metadata.

---

## Requirements

- **Python 3.10+** (3.11+ recommended)
- Dependencies in **`requirements.txt`**

---

## Quick start (local)

```bash
cd profiles_mess
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

Copy **`.env.example`** to **`.env`** and set variables if needed (see below).

```bash
streamlit run app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`).

On first use, open the sidebar and **Download / refresh GTFS** so `data/current/` contains the feed.

---

## Configuration (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `NTA_API_KEY` | No | NTA developer portal subscription key, if you use an API-hosted GTFS URL that expects `Ocp-Apim-Subscription-Key`. |
| `GTFS_DOWNLOAD_URL` | No | Override for the static GTFS zip. Default is the public TFI Dublin Bus bundle URL. |

If you omit both, the default public URL is used (no key).

---

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit UI |
| `auditor/` | GTFS download, calendar, trip matching, segment geometry, map, Excel export, URL state |
| `data/current/` | Unzipped GTFS (gitignored; created after download) |
| `prd.md` | Product notes |

---

## GTFS updates

The app **does not** download a new feed on a timer. Use **Download / refresh GTFS** when you want the latest published schedule. For hosting, set a **cron** or startup job if you need automatic refreshes.

---

## Streamlit Community Cloud (recommended)

Good fit for this app: **HTTPS**, GitHub deploy, no server to manage.

1. Push this repo to GitHub (e.g. `profileissues`).
2. Sign in at **[share.streamlit.io](https://share.streamlit.io)** with GitHub.
3. **Create app** → pick the repo, branch **`main`**, main file **`app.py`**.
4. **App settings → Secrets** (TOML). Optional keys — omit or leave empty to use the default public GTFS URL:

   ```toml
   # Optional — only if you use a custom API-hosted feed or URL override
   # NTA_API_KEY = "your-key-here"
   # GTFS_DOWNLOAD_URL = "https://..."
   ```

   The app reads these via `st.secrets` (and still supports local `.env`).

5. **Advanced settings** (if offered): choose a **Python** version that matches local dev (e.g. 3.11 or 3.12).

6. Deploy. First visit: open the sidebar and use **Download / refresh GTFS** (the `data/current/` folder is empty on a fresh instance until you do).

**Note:** Free apps may **sleep** when idle; the next load can take a few seconds. GTFS is stored on the instance filesystem and may be **lost after restarts** — download again when needed, or accept a cold start after idle.

---

## Other hosts (Docker / PaaS)

Streamlit is a long-running process; **Vercel** is not a good fit. Use a container host or PaaS (e.g. **Render**, **Fly.io**, **Railway**) with:

```bash
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0
```

Set environment variables in the host dashboard, not in the repo.

---

## Changelog

This project uses **[CHANGELOG.md](CHANGELOG.md)** in the [Keep a Changelog](https://keepachangelog.com/) style.

**When you ship changes:** add a bullet under **`[Unreleased]`**, then move those items into a new **`[x.y.z]`** section with a date when you tag a release.

---

## Disclaimer

This tool is for **schedule analysis** using published GTFS. It is not affiliated with Dublin Bus or the NTA. Verify safety and operations against official policies and your own judgement.
