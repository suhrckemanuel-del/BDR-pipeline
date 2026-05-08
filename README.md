# BDR Pipeline

A white-label, agentic BDR (Business Development Representative) pipeline. Point it at a company name and it produces a full 5-touch outreach sequence — researched, positioned, drafted, and quality-graded — for the tenant you have configured.

Built on **LangGraph**, **Claude**, and **Streamlit**. Tenant-driven: all positioning, ICP, angles, and copy banks live in `tenants/<slug>/` config files, not in code.

> _Screenshot placeholder — `docs/screenshot.png`_

---

## 30-second tour

```
   Company name
        │
        ▼
┌──────────────────┐
│ 1. Enrichment    │  Exa live signals + Hunter.io contacts +
│                  │  Claude research summary + ICP tier
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 2. Strategist    │  Claude picks 1 of 3 tenant-defined angles
│                  │  (structured output)
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 3. Humanizer     │  LLM writes observations only; deterministic
│                  │  assembler glues in fixed-bank proof points
│                  │  and CTAs. 5-touch sequence built here.
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 4. Critic        │  Claude scores each touch on 4 dimensions
│                  │  and rewrites the first paragraph of any
│                  │  failing email touch.
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 5. CRM Sync      │  Optional — push prospect card to Notion.
└──────────────────┘
```

The output is a `ProspectCard`: 3 angle drafts (DM + email subject + email body) plus a 5-touch sequence (LinkedIn connect → email → follow-up → social proof → breakup).

---

## Quickstart

```bash
git clone https://github.com/suhrckemanuel-del/BDR-pipeline.git
cd BDR-pipeline

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# fill in ANTHROPIC_API_KEY, EXA_API_KEY, HUNTER_API_KEY (the rest are optional)

streamlit run app/main.py
```

The UI opens at `http://localhost:8501`. The sidebar dropdown lets you pick a tenant; the bundled `demo` tenant (Acme Analytics, fictional B2B SaaS) is selected by default.

To pin a tenant for scripted runs:

```bash
BDR_TENANT=demo streamlit run app/main.py
```

---

## Configure your own tenant

A **tenant** is one positioning of the pipeline: a product, an ICP, three outreach angles, brand strings, and a sender identity. To add yours:

**Option A — Interactive wizard (recommended):**

```bash
python scripts/onboard_tenant.py
```

The wizard asks you 8 questions, then has Claude draft a complete tenant skeleton (`config.yaml`, `icp.txt`, `angles.json`, `copy.json`). ~3 minutes for a working draft.

**Option B — Copy and edit by hand:**

```bash
cp -r tenants/demo tenants/my-tenant
python scripts/check_tenant.py my-tenant   # validate
```

Full schema reference and field-by-field walkthrough: [`tenants/README.md`](tenants/README.md).

---

## Architecture

```
app/
  main.py                 # Streamlit dashboard — tenant dropdown + run UI
  agents/
    state.py              # BDRState TypedDict + Pydantic schemas
    workflow_engine.py    # LangGraph DAG (5 linear nodes)
    enrichment.py         # Exa + Hunter + ICP classification
    strategist.py         # Claude picks 1 of 3 angles
    humanizer.py          # Observations + deterministic copy assembly
    critic.py             # 4-dim LLM scoring + per-touch rewrites
  services/
    crm_sync.py           # Notion push (schema-agnostic)
    gmail_sender.py       # Outbound queue (per-tenant)
    humanizer_rules.py    # 29-rule anti-AI text filter
  tenants/
    schema.py             # Pydantic v2 TenantConfig (extra="forbid")
    loader.py             # load_tenant() — cached, validates on read

tenants/
  demo/                   # Bundled example tenant (Acme Analytics)
    config.yaml
    icp.txt
    angles.json
    copy.json
    data/prospects.csv

scripts/
  onboard_tenant.py       # Claude-assisted tenant wizard
  check_tenant.py         # Validate one or all tenants
  send_via_gmail.py       # Send a tenant's queued sequences (--dry-run supported)
```

### The anti-AI copy pattern

LLMs sound like LLMs. We work around this by letting Claude generate **only observations** (3 specific things it noticed about the prospect, plus a Before/After narrative) and then having a **deterministic assembler** glue those observations into emails using human-written **fixed copy banks** — proof points, CTAs, subject lines, follow-ups, breakups, all 33 strings per angle, all reviewed by you.

The same prospect always picks the same variant from each bank (hash-based), so reruns are stable.

A final 29-rule regex filter strips residual LLM tells (`actually,`, `transformative`, `leverage`, etc.) on the way out.

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Claude API — used by every agent |
| `EXA_API_KEY` | yes | Live signals (news, jobs). Pipeline still runs without it, with fewer signals. |
| `HUNTER_API_KEY` | yes | Contact discovery on the prospect's domain. Pipeline still runs without it. |
| `NOTION_API_KEY` | optional | Required only if `crm.enabled: true` in a tenant config. |
| `NOTION_DATABASE_ID` | optional | Default Notion database. Per-tenant override available in `config.yaml`. |
| `GMAIL_SENDER` | optional | Required only for `scripts/send_via_gmail.py`. |
| `GMAIL_APP_PASSWORD` | optional | Gmail App Password (requires 2FA). |
| `BDR_TENANT` | optional | Pin a tenant slug. Disables the sidebar dropdown when set. |
| `LANGCHAIN_API_KEY` | optional | LangSmith tracing. See `.env.example`. |

See [`.env.example`](.env.example) for the canonical list.

---

## Roadmap

- **Signal-weighted ICP scoring** — replace 3-tier classification with a 0–100 composite (funding, hiring signals, headcount band, geography, technographics).
- **Per-tenant model overrides** — let `config.yaml` choose Sonnet vs. Opus per agent.
- **SQLite persistence** — replace CSV state with a queryable run log.
- **Multiple sequence variants per tenant** — e.g. founder track vs. enterprise track.
- **Per-tenant Exa query templates** — let tenants override the default signal queries.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Author

Manuel Suhrcke — [github.com/suhrckemanuel-del](https://github.com/suhrckemanuel-del)
