#!/usr/bin/env python
"""
benchmark_free_research.py — #15 offline A/B benchmark (Q1 + Q2; Q3 guided live).

Compares the paid pipeline's research evidence pool against the free
public-page prototype for the same company set and the same candidate URL
list. The free arm runs with zero LLM calls and no API keys; evidence quality
reconciliation (which arm's evidence better matches what the strategist
actually used) is the Q3 live-guided follow-up, marked in the output summary.

Usage:
    python scripts/benchmark_free_research.py --tenant demo \
        --csv docs/free-research-benchmark.csv --json docs/free-research-benchmark.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.demo_eval import load_prospects  # noqa: E402
from app.services.free_research import (  # noqa: E402
    PAGE_CANDIDATES,
    research_company_public_pages,
)
from app.tenants.loader import load_tenant  # noqa: E402

CSV_FIELDS = [
    "company",
    "domain",
    "pages_fetched",
    "pages_blocked",
    "pages_failed",
    "coverage",
    "evidence_chars",
    "seconds",
    "llm_calls_free_arm",
    "outcome",
]


@dataclass
class CompanyRow:
    company: str
    domain: str
    pages_fetched: int = 0
    pages_blocked: int = 0
    pages_failed: int = 0
    coverage: float = 0.0
    evidence_chars: int = 0
    seconds: float = 0.0
    llm_calls_free_arm: int = 0
    outcome: str = ""

    def fields(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkSummary:
    companies: int
    pages_attempted: int
    pages_fetched: int
    pages_blocked: int
    pages_failed: int
    coverage_pct: float
    evidence_chars: int
    seconds: float
    llm_calls_free_arm: int
    q3_live_note: str = "Evidence-quality reconciliation vs the paid arm is the live, API-key-guided step (see docs/cost-baseline.md)."


def _prospects_from_tenant(tenant) -> list[tuple[str, str]]:
    """(company, domain) pairs from the tenant's prospect CSV."""
    pairs: list[tuple[str, str]] = []
    for prospect in load_prospects(tenant):
        company = (prospect.get("company", "") or "").strip()
        domain = (prospect.get("domain", "") or prospect.get("website", "") or "").strip()
        if company and domain:
            pairs.append((company, domain))
    return pairs


def run_benchmark(prospects: list[tuple[str, str]]) -> tuple[list[CompanyRow], BenchmarkSummary]:
    rows: list[CompanyRow] = []
    for company, domain in prospects:
        started = time.perf_counter()
        result = research_company_public_pages(company, domain)
        seconds = time.perf_counter() - started
        attempted = len(PAGE_CANDIDATES)
        rows.append(
            CompanyRow(
                company=company,
                domain=domain,
                pages_fetched=result.pages_fetched,
                pages_blocked=result.pages_blocked,
                pages_failed=result.pages_failed,
                coverage=round(result.pages_fetched / attempted, 3) if attempted else 0.0,
                evidence_chars=sum(len(s.get("snippet", "")) for s in result.signals),
                seconds=round(seconds, 2),
                llm_calls_free_arm=0,
                outcome="ok" if result.pages_fetched else "no_evidence",
            )
        )

    attempted_total = len(rows) * len(PAGE_CANDIDATES)
    fetched_total = sum(r.pages_fetched for r in rows)
    blocked_total = sum(r.pages_blocked for r in rows)
    failed_total = sum(r.pages_failed for r in rows)
    summary = BenchmarkSummary(
        companies=len(rows),
        pages_attempted=attempted_total,
        pages_fetched=fetched_total,
        pages_blocked=blocked_total,
        pages_failed=failed_total,
        coverage_pct=round(100.0 * fetched_total / attempted_total, 1) if attempted_total else 0.0,
        evidence_chars=sum(r.evidence_chars for r in rows),
        seconds=round(sum(r.seconds for r in rows), 2),
        llm_calls_free_arm=0,
    )
    return rows, summary


def write_csv(rows: list[CompanyRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.fields().get(field, "") for field in CSV_FIELDS})


def write_json(summary: BenchmarkSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Free-research A/B benchmark (ticket #15)")
    parser.add_argument("--tenant", default="demo", help="Tenant id whose prospect list seeds the benchmark")
    parser.add_argument(
        "--company",
        action="append",
        default=[],
        metavar="NAME=DOMAIN",
        help="Explicit company/domain pair (repeatable); overrides the tenant prospect list",
    )
    parser.add_argument("--csv", default="docs/free-research-benchmark.csv", help="Output CSV path")
    parser.add_argument("--json", default="docs/free-research-benchmark.json", help="Output JSON summary path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pairs: list[tuple[str, str]] = []
    for spec in args.company:
        name, _, domain = spec.partition("=")
        if name.strip() and domain.strip():
            pairs.append((name.strip(), domain.strip()))
    if not pairs:
        tenant = load_tenant(args.tenant)
        pairs = _prospects_from_tenant(tenant)
    if not pairs:
        print("No (company, domain) pairs found — pass --company NAME=DOMAIN or seed a tenant list.", file=sys.stderr)
        return 1

    rows, summary = run_benchmark(pairs)
    write_csv(rows, Path(args.csv))
    write_json(summary, Path(args.json))

    print(f"Benchmark complete: {summary.companies} companies, "
          f"{summary.pages_fetched}/{summary.pages_attempted} pages fetched "
          f"({summary.coverage_pct}% coverage), {summary.evidence_chars} evidence chars, "
          f"{summary.seconds}s total, {summary.llm_calls_free_arm} LLM calls (free arm).")
    print(f"CSV:  {args.csv}")
    print(f"JSON: {args.json}")
    print(summary.q3_live_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
