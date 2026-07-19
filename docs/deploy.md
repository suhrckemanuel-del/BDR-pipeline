# Deploying the BDR Pipeline

The app is a single Streamlit process with file-based state: a SQLite database
(runs, prospect funnel, events) plus per-tenant queue files. Deployment is
mostly a question of **where that state lives** and **which API keys you set**.

## State: where it lives

| State | Default location | Override |
|---|---|---|
| Runs / funnel / events (SQLite) | `pipeline/bdr.db` | `BDR_DB_PATH=/path/to/bdr.db` |
| Queued outbound sequences | `tenants/<tenant_id>/data/queued/*.json` | — (always under the repo tree) |
| Tenant configs | `tenants/<slug>/` | — (checked into git) |

`BDR_DB_PATH` is read on every store call, so pointing it at a mounted volume
is all the persistence setup the database needs.

## Zero-key demo mode

The app boots and is fully demoable with **no API keys at all**:

- The dashboard starts, tenants load, and all four workspaces render.
- **Batch runs → sample mode** executes the whole pipeline offline
  (deterministic sample states, eval gates, funnel persistence).
- `python scripts/run_eval.py` and `python scripts/run_batch.py --limit 3`
  work the same way.

Keys unlock the live paths: `ANTHROPIC_API_KEY` (agents), `EXA_API_KEY`
(signals), `HUNTER_API_KEY` (contacts), and the optional integrations
(Notion, HubSpot, Gmail). Every integration degrades gracefully — a missing
key produces a skip message, never a crash.

## Streamlit Community Cloud

Works for demos, with one hard caveat:

> **Streamlit Cloud's filesystem is ephemeral. The SQLite database (and any
> queued sequences) are wiped on every redeploy, reboot, or app update.**
> Use it only when losing run history is acceptable — for anything you want
> to keep, deploy to a VPS with a volume (below).

Setup:

1. Point the app at `app/main.py`.
2. Add keys in **App settings → Secrets** (TOML). `app/main.py` hydrates
   `ANTHROPIC_API_KEY`, `EXA_API_KEY`, `HUNTER_API_KEY`, `NOTION_API_KEY`,
   `NOTION_DATABASE_ID`, `LANGCHAIN_API_KEY`, and `BDR_TENANT` from
   `st.secrets` automatically.
3. Optionally pin the tenant with `BDR_TENANT = "demo"` in secrets.

## VPS / Docker (recommended for persistence)

The repo ships a `Dockerfile` (and a `.dockerignore` that keeps the ~180 MB of
demo media out of the build context). The image sets `BDR_DB_PATH=/data/bdr.db`
so the database lands on a volume.

```bash
# On the server, from the repo root:
docker build -t bdr-pipeline .

cp .env.example .env   # fill in whichever keys you have — none are required to boot

docker run -d --name bdr \
  -p 8501:8501 \
  -v bdr-data:/data \
  --env-file .env \
  --restart unless-stopped \
  bdr-pipeline
```

- `-v bdr-data:/data` — named volume for the SQLite DB. This is what survives
  `docker rm` + redeploy.
- To also persist queued sequences across container replacement, mount the
  tenants tree: `-v /srv/bdr/tenants:/app/tenants` (seed it with the repo's
  `tenants/` first).
- Upgrade: `git pull && docker build -t bdr-pipeline . && docker rm -f bdr`
  then re-run the `docker run` command. The volume carries the data forward.

Health check: the image polls `/_stcore/health`; `docker ps` shows
`(healthy)` once Streamlit is up. Put nginx/Caddy in front for TLS if the
dashboard is exposed publicly.

## Bare VPS (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys

BDR_DB_PATH=/srv/bdr/bdr.db BDR_TENANT=demo \
  streamlit run app/main.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
```

Run it under systemd or a process manager; back up `/srv/bdr/bdr.db` (it's a
single file — `sqlite3 bdr.db ".backup backup.db"` is enough).
