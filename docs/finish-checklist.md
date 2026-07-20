# Finish Checklist — Live Verification on Your Machine

Everything on branch `claude/bdr-phase-3-scoring-push-02k1er` (PR #2) is
implemented and passes the full offline check suite + CI. What has **not**
happened yet is a run against real APIs — no key has ever been used. This doc
is the shortest path from `git clone` to "verified live, safe to merge."

---

## 1. Clone and set up

```bash
git clone https://github.com/suhrckemanuel-del/BDR-pipeline.git
cd BDR-pipeline
git checkout claude/bdr-phase-3-scoring-push-02k1er

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt

cp .env.example .env               # then fill in keys (next section)
```

Sanity check before any keys (should all pass — this is what CI runs):

```bash
python scripts/check_tenant.py
python scripts/run_eval.py
python scripts/check_crm_sync.py
python scripts/check_exports.py
python scripts/check_scoring.py
python scripts/check_push.py
python scripts/check_config_overrides.py
python scripts/check_variants.py
```

## 2. Keys — what you need and where to get each one

Put them in `.env` (gitignored, never committed).

### Required for the core pipeline

| Key | Where to get it | What breaks without it |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/api-keys) → API Keys → Create Key | Nothing runs live — every agent needs it |
| `EXA_API_KEY` | [dashboard.exa.ai](https://dashboard.exa.ai) → API Keys | Pipeline still runs, but no live news/job signals |
| `HUNTER_API_KEY` | [hunter.io](https://hunter.io/api-keys) → API → API Key (free tier: 25 searches/mo) | Pipeline still runs, but no contact discovery |

### Optional — only for the integrations you actually use

| Key | Where to get it | Needed when |
| --- | --- | --- |
| `NOTION_API_KEY` + `NOTION_DATABASE_ID` | [notion.so/my-integrations](https://www.notion.so/my-integrations) → New integration; share your DB with it; DB id is in the DB URL | Tenant `crm.provider: notion` + `crm.enabled: true` |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot → Settings → Integrations → Private Apps → Create (scopes: crm.objects companies/contacts/notes read+write; free tier OK) | Tenant `crm.provider: hubspot` |
| `SALESFORCE_ACCESS_TOKEN` + `SALESFORCE_INSTANCE_URL` | Easiest: install the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli), `sf org login web`, then `sf org display` shows both. A [Developer Edition org](https://developer.salesforce.com/signup) is free | Tenant `crm.provider: salesforce` |
| `PIPEDRIVE_API_TOKEN` | Pipedrive → click your avatar → Personal preferences → API | Tenant `crm.provider: pipedrive` |
| `INSTANTLY_API_KEY` | Instantly → Settings → Integrations → API Keys | Live push: tenant `outreach.instantly_campaign_id` set |
| `SMARTLEAD_API_KEY` | Smartlead → Settings → Smartlead API Key | Live push: tenant `outreach.smartlead_campaign_id` set |
| `GMAIL_SENDER` + `GMAIL_APP_PASSWORD` | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (needs 2FA on the account) | Sending queued sequences / reply tracking |

Per-tenant keys: instead of the shared env vars above, a tenant's `config.yaml`
can name its own env var (`crm.hubspot_token_env`, `crm.salesforce_token_env`,
`crm.pipedrive_token_env`, `outreach.instantly_api_key_env`,
`outreach.smartlead_api_key_env`). Tokens themselves never go in config files.

## 3. Live smoke test (the actual unfinished work)

Highest-risk first — these code paths have **never touched a real API**, and
the exact endpoint/field shapes for Instantly/Smartlead/Salesforce/Pipedrive
are implemented from docs, not verified against the wire. Each one routes
through a single `_api` function, so any mismatch is a small localized fix
(in `app/services/sequence_push.py`, `salesforce_sync.py`, `pipedrive_sync.py`).

**Step 1 — one live pipeline run** (needs the 3 required keys):

```bash
streamlit run app/main.py
```

Run one prospect (sidebar → pick a demo prospect → Run pipeline). Confirm:
live signals appear, contacts resolve, the sequence assembles, the critic
scores it, and the score breakdown renders. This validates enrichment,
model overrides, and Exa templates against real APIs.

**Step 2 — live push, dry-run first** (needs a sending-tool key + a test campaign):

1. Create an empty test campaign in Instantly (or Smartlead) and copy its id.
2. In your tenant `config.yaml`: `outreach: { instantly_campaign_id: "<id>" }`.
3. In the tracker, set one eval-passed prospect to `queued`, then with
   `BDR_PUSH_DRY_RUN=1` in `.env` click **Push queued leads** — inspect that
   payloads build.
4. Remove the dry-run flag, push again, and check the lead landed in the
   campaign. If the API rejects it, the error surfaces per-prospect in the
   UI — fix the path/field in `sequence_push.py` and retry.

**Step 3 — CRM connector (whichever you use)**: set `crm.provider` +
`crm.enabled: true` in the tenant, `BDR_CRM_DRY_RUN=1` first, then a real
sync of one prospect. Verify the company/contact/note appear in the CRM.

**Step 4 — re-run the offline suite** (`python scripts/run_eval.py` etc.) to
confirm nothing you touched regressed, commit any wire fixes to this branch,
and merge PR #2.

## 4. Known notes

- `scripts/run_eval.py --strict` fails on fixture-completeness gates — this
  predates Phase 3 and is expected; non-strict is the CI gate.
- Docker: `docker build -t bdr-pipeline .` — CI already proves this builds.
- Nothing sends email unless you explicitly run `scripts/send_via_gmail.py`.
