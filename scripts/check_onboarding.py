"""
Offline regression checks for website-driven tenant onboarding.

Exercises the --url plumbing in scripts/onboard_tenant.py with the network and
LLM mocked out: HTML text extraction, homepage fail-loud behavior, local-file
fetching, the shared write/summarize/validate chain, and argparse backward
compatibility. No API keys, no network. Exit 1 on any failed assertion.

    python scripts/check_onboarding.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import onboard_tenant as ob  # noqa: E402

from app.tenants import list_tenants, load_tenant  # noqa: E402
from app.tenants.schema import HumanizerCopy, OutreachAngle  # noqa: E402

CHECK_SLUG = "zz-check-onboarding"

HOME_HTML = """<!doctype html>
<html><head>
<title>Acme Test — Revenue Intelligence</title>
<meta name="description" content="Acme Test helps B2B sales teams forecast with confidence.">
<style>body { color: red; } .SECRET-CSS-TOKEN {}</style>
<script>var SECRET_JS_TOKEN = 1;</script>
</head><body>
<nav>Home &amp; About &amp; Pricing</nav>
<h1>Forecast with confidence</h1>
<p>Acme Test plugs into your CRM and scores every deal in the pipeline.</p>
<noscript>SECRET-NOSCRIPT-TOKEN</noscript>
</body></html>"""

ABOUT_HTML = "<html><head><title>About</title></head><body><p>Founded in 2020 by revenue operators.</p></body></html>"


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    # 1. html_to_text strips code and markup, keeps visible copy.
    text = ob.html_to_text(HOME_HTML)
    check("text_keeps_visible_copy", "scores every deal" in text and "Forecast with confidence" in text)
    check("text_keeps_meta_description", "forecast with confidence." in text.lower())
    check("text_drops_script_style_noscript", "SECRET" not in text, text[:200])
    check("text_no_markup", "<" not in text)
    check("text_collapses_whitespace", "  " not in text)
    check("text_respects_max_chars", len(ob.html_to_text(HOME_HTML * 50, max_chars=500)) <= 500)

    # 2. fetch_site_content: happy path with an injected fetcher.
    seen: list[str] = []

    def fake_fetcher(url: str, timeout: float) -> str:
        seen.append(url)
        if url == "https://acme.test":
            return HOME_HTML
        if url == "https://acme.test/about":
            return ABOUT_HTML
        raise RuntimeError("404")

    pages = ob.fetch_site_content("acme.test", fetcher=fake_fetcher)
    check("fetch_scheme_normalized", seen[0] == "https://acme.test", str(seen[:1]))
    check("fetch_pages_keys", set(pages) == {"/", "/about"}, str(set(pages)))
    check("fetch_secondary_best_effort", "/pricing" not in pages)

    # 3. Fail-loud on unreachable homepage; secondary failures stay silent.
    def dead_fetcher(url: str, timeout: float) -> str:
        raise RuntimeError("connection refused")

    try:
        ob.fetch_site_content("dead.invalid", fetcher=dead_fetcher)
        check("homepage_failure_raises", False)
    except ob.OnboardingError as exc:
        check("homepage_failure_raises", True)
        check("failure_message_names_url", "dead.invalid" in str(exc), str(exc))

    # 4. file:// / local-path fetching needs no mock and no network.
    scratch = Path(tempfile.mkdtemp(prefix="bdr-check-onboarding-"))
    try:
        fixture = scratch / "home.html"
        fixture.write_text(HOME_HTML, encoding="utf-8")
        local_pages = ob.fetch_site_content(str(fixture))
        check("local_path_fetch", "scores every deal" in local_pages.get("/", ""))

        # 5. End-to-end write/summarize/validate with the LLM mocked.
        skeleton = ob.skeleton_draft()
        brief = {
            "brand_name": "Acme Test",
            "short_name": "Acme",
            "tagline": "Forecast with confidence",
            "primary_color": "#2563EB",
            "business_description": "Acme Test scores every deal in the pipeline for B2B sales teams.",
            "headline_metric": "",
            "reference_customers": [],
            "persona_title": "VP of Sales",
            "persona_alternates": [],
            "sender_name": "Jane Doe",
        }
        target_dir = ROOT / "tenants" / CHECK_SLUG
        try:
            ob.write_tenant_files(target_dir, brief, skeleton)
            summary = ob.summarize_draft(CHECK_SLUG, target_dir, brief, skeleton)
            check("summary_lists_config", f"+ tenants/{CHECK_SLUG}/config.yaml" in summary, summary)
            check("summary_lists_brand", 'brand.name: "Acme Test"' in summary, summary)
            check(
                "summary_lists_angles",
                all(a["name"] in summary for a in skeleton["angles"]),
                summary,
            )
            tenant = ob.validate_tenant(CHECK_SLUG)
            check("written_tenant_validates", tenant.brand.name == "Acme Test")
            check("config_roundtrips_sender", tenant.sender.name == "Jane Doe")
        finally:
            shutil.rmtree(target_dir, ignore_errors=True)
            load_tenant.cache_clear()
        check("check_tenant_cleaned_up", CHECK_SLUG not in list_tenants())

        # 6. Parser stays backward compatible.
        parser = ob.build_parser()
        legacy = parser.parse_args(["--slug", "x", "--no-llm", "--force"])
        check("parser_legacy_flags", legacy.slug == "x" and legacy.no_llm and legacy.force and legacy.url is None)
        url_args = parser.parse_args(["--url", "acme.com", "--sender", "Jane"])
        check("parser_url_flags", url_args.url == "acme.com" and url_args.sender == "Jane")

        # 7. Skeleton draft still satisfies the tenant schema.
        angles_ok = all(isinstance(OutreachAngle(**a), OutreachAngle) for a in skeleton["angles"])
        copy_ok = isinstance(HumanizerCopy(angles=skeleton["copy"]), HumanizerCopy)
        check("skeleton_still_validates", angles_ok and copy_ok)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} onboarding check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All onboarding checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
