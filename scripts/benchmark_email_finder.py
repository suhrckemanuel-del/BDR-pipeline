#!/usr/bin/env python
"""
benchmark_email_finder.py — C1 (#17) live A/B: free email finder vs Hunter.

Free arm: robots-respecting public-page recon + pattern inference (no names
supplied, so recon only) — zero API keys, zero cost.
Hunter arm: the production `_fetch_hunter_contacts` domain-search call with
the demo tenant's persona filter — the exact comparison an operator cares
about: what would the pipeline have consumed for the same domain?

Usage:
    python scripts/benchmark_email_finder.py --tenant demo \
        --company "Stripe=stripe.com"            # overrides the default set
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.enrichment import _fetch_hunter_contacts  # noqa: E402
from app.services.email_finder import find_emails  # noqa: E402
from app.tenants.loader import load_tenant  # noqa: E402

# Default set mirrors the #15 research benchmark for comparability.
DEFAULT_COMPANIES = [
    ("Stripe", "stripe.com"),
    ("Notion", "notion.com"),
    ("Linear", "linear.app"),
    ("Vercel", "vercel.com"),
    ("Figma", "figma.com"),
]

CSV_FIELDS = [
    "company", "domain",
    "free_emails_found", "free_best_conf", "free_best_email", "free_named",
    "free_conventions", "free_pages_fetched", "free_seconds",
    "hunter_contacts", "hunter_best_conf", "hunter_best_email",
    "outcome",
]


@dataclass
class CompanyRow:
    company: str
    domain: str
    free_emails_found: int = 0
    free_best_conf: int = 0
    free_best_email: str = ""
    free_named: int = 0
    free_conventions: str = ""
    free_pages_fetched: int = 0
    free_seconds: float = 0.0
    hunter_contacts: int = 0
    hunter_best_conf: int = 0
    hunter_best_email: str = ""
    outcome: str = ""

    def fields(self) -> dict:
        return asdict(self)


def _usable(found_list) -> int:
    """Addresses an operator could plausibly use (not noreply-class junk)."""
    return sum(1 for f in found_list if f.confidence >= 50)


def run_company(company: str, domain: str, tenant) -> CompanyRow:
    row = CompanyRow(company=company, domain=domain)

    started = time.perf_counter()
    free = find_emails(company, domain)
    row.free_seconds = round(time.perf_counter() - started, 2)
    row.free_emails_found = len(free.emails)
    row.free_named = _usable(free.emails)
    row.free_conventions = "|".join(free.conventions)
    row.free_pages_fetched = free.pages_fetched
    if free.emails:
        best = free.emails[0]
        row.free_best_conf = best.confidence
        row.free_best_email = best.email

    if os.environ.get("HUNTER_API_KEY"):
        try:
            _domain, contacts, _degs = _fetch_hunter_contacts(company, tenant, limit=10)
            row.hunter_contacts = len(contacts)
            if contacts:
                best_h = max(contacts, key=lambda c: c.confidence)
                row.hunter_best_conf = int(best_h.confidence)
                row.hunter_best_email = best_h.email or ""
        except Exception as exc:  # benchmark must not die on one domain
            row.hunter_best_email = f"error: {type(exc).__name__}"
    else:
        row.hunter_best_email = "(no HUNTER_API_KEY)"

    row.outcome = (
        f"free_hit={row.free_named > 0}" if row.free_named
        else ("free_partial" if row.free_emails_found else "free_miss")
    )
    return row


def run_benchmark(pairs: list[tuple[str, str]], tenant) -> list[CompanyRow]:
    return [run_company(company, domain, tenant) for company, domain in pairs]


def print_table(rows: list[CompanyRow]) -> None:
    print(f"\n{'Company':<10} {'Free emails':>11} {'Free usable':>11} {'Free best':>26} {'Hunter':>8} {'Hunter best':>28}")
    print("-" * 100)
    for r in rows:
        print(f"{r.company:<10} {r.free_emails_found:>11} {r.free_named:>11} "
              f"{(r.free_best_email[:24] + ('…' if len(r.free_best_email) > 24 else '')):>26} "
              f"{r.hunter_contacts:>8} "
              f"{(r.hunter_best_email[:26] + ('…' if len(r.hunter_best_email) > 26 else '')):>28}")


def write_csv(rows: list[CompanyRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.fields().get(k, "") for k in CSV_FIELDS})


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Free email finder vs Hunter benchmark (ticket #17)")
    parser.add_argument("--tenant", default="demo")
    parser.add_argument("--company", action="append", default=[], metavar="NAME=DOMAIN",
                        help="Explicit company/domain pair (repeatable); overrides the default set")
    parser.add_argument("--csv", default="docs/email-finder-benchmark.csv")
    parser.add_argument("--json", default="docs/email-finder-benchmark.json")
    args = parser.parse_args()

    pairs: list[tuple[str, str]] = []
    for spec in args.company:
        name, _, domain = spec.partition("=")
        if name.strip() and domain.strip():
            pairs.append((name.strip(), domain.strip()))
    if not pairs:
        pairs = DEFAULT_COMPANIES

    tenant = load_tenant(args.tenant)
    rows = run_benchmark(pairs, tenant)
    write_csv(rows, Path(args.csv))
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps([r.fields() for r in rows], indent=2), encoding="utf-8")

    print_table(rows)
    free_hit = sum(1 for r in rows if r.free_named > 0)
    hunter_hit = sum(1 for r in rows if r.hunter_contacts > 0)
    print(f"\nSummary: free usable-contact hit rate {free_hit}/{len(rows)} · "
          f"Hunter hit rate {hunter_hit}/{len(rows)} · free arm cost $0 (no API keys, no LLM calls)")
    print(f"CSV:  {args.csv}\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
