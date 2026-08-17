"""Check the published app from outside it, the way a reader meets it.

Every other gate in this repository runs against code on a machine we control. On
2026-08-16 that turned out not to be the same thing as a working deployment. The
public app served an `ImportError` on two of its nine pages for two and a half hours
while `verify.ps1` was green, `pytest` was green and 145 dbt checks were green.

The cause was not in the code. Streamlit Community Cloud failed to pull the new
commit eight consecutive times -- `Updating the app files has failed: exit status 1`
at 05:04, 05:15, 05:31, 05:46, 05:50, 06:03, 06:22 and 07:26 -- and reported it only
in a log nobody was reading. The container stayed on the commit it had cloned at
04:59, whose `dashboard/runtime.py` genuinely predated `cached_contiguous_windows`
and `cached_model_station_discrepancy`, so the newer page modules could not import.
A reader saw a redacted error. Nobody was told.

Nothing in the project could have caught it, because nothing looked at the
deployment. This does, and it is the only check here that talks to the internet by
design rather than by accident.

**Why not an HTTP status check.** Streamlit answers 200 with a shell document before
a single line of the app has run, so `curl` reports a healthy app while every page
inside it is failing. That is a check which cannot fail on broken code, which this
project holds to be worse than no check at all.

**Scope, stated precisely because it is narrower than "the app works".** Each page
must render the heading its own `st.title` sets, and no page may raise. That is the
class that shipped: a page that cannot run. It deliberately does NOT check layout,
accessibility, or data freshness -- `verify_layout.py` and `verify_a11y.py` measure
geometry against a local server, and freshness is a data property the app displays
for itself. A page that renders an honest empty state passes here, and should: an
empty state is the app working.

Two consequences of running against a real deployment, both deliberate:

* **One retry per page.** A scheduled check that flaps gets ignored, and a cold
  Community Cloud container can take longer than a local one to answer. A page is
  only reported after it has failed twice.
* **A sleeping app fails as a missing heading.** Community Cloud parks idle apps
  behind an interstitial. No string from that screen is asserted here, because none
  has been measured from this app; it presents as the heading never arriving, which
  is the correct verdict for a public app that is not serving.

    python scripts/verify_live_app.py
    python scripts/verify_live_app.py --base-url http://localhost:8501

Needs the `qa` extra and a browser:

    pip install -e ".[qa]"
    python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

try:
    from playwright.sync_api import Page, sync_playwright
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
except ModuleNotFoundError:  # pragma: no cover - guidance, not logic
    print(
        'playwright is not installed. Run: pip install -e ".[qa]" '
        "&& python -m playwright install chromium",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

DEFAULT_BASE_URL = "https://vn-air-quality-weather.streamlit.app"

# Path, and the heading that page's own st.title sets. The heading is what makes each
# entry discriminating: st.navigation serves an unknown path by falling back to the
# default page, so asserting "something rendered" would let a broken /trust pass on
# the strength of Today's heading. Asserting the page's own title cannot.
PAGES: tuple[tuple[str, str], ...] = (
    ("/", "Không khí hôm nay"),
    ("/forecast", "Dự báo 24–72 giờ"),
    ("/custom_location", "Địa điểm tùy chọn"),
    ("/national_map", "Bản đồ chất lượng không khí Việt Nam"),
    ("/compare", "So sánh địa điểm"),
    ("/history", "Lịch sử và xu hướng"),
    ("/alerts", "Cảnh báo cá nhân"),
    ("/trust", "Độ tin cậy dữ liệu"),
    ("/pipeline_health", "Pipeline health"),
)

# Streamlit renders an uncaught exception into this test id; confirmed present in the
# 1.60 frontend bundle. Community Cloud redacts the message in the browser and keeps
# the detail in its own log, so the text captured here can be a redaction notice --
# which is still worth printing, because it names the page and proves the raise.
EXCEPTION_SELECTOR = '[data-testid="stException"]'
MAIN_SELECTOR = '[data-testid="stMain"]'

GOTO_TIMEOUT_MS = 60_000
MAIN_TIMEOUT_MS = 45_000
HEADING_TIMEOUT_MS = 45_000

HEADING_PRESENT_JS = """
(heading) => {
  const main = document.querySelector('[data-testid="stMain"]');
  return !!main && main.innerText.includes(heading);
}
"""

COLLECT_JS = """
() => ({
  exceptions: [...document.querySelectorAll('[data-testid="stException"]')]
    .map((node) => (node.innerText || '').trim().slice(0, 300)),
  mainText: (document.querySelector('[data-testid="stMain"]')?.innerText || '')
    .trim().slice(0, 200),
})
"""


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    detail: str


def page_url(base_url: str, path: str) -> str:
    """Address the app itself, not the page that frames it.

    A `*.streamlit.app` address serves a wrapper document with the real app in an
    iframe, so a DOM read at the top level finds none of the app's own markup. The
    `/~/+` prefix is the app served directly. A localhost server has no wrapper and
    needs no prefix.
    """

    if base_url.endswith(".streamlit.app"):
        return f"{base_url}/~/+{path}"
    return f"{base_url}{path}"


def inspect_page(page: Page, base_url: str, path: str, heading: str) -> list[str]:
    """Load one page and report what is wrong with it, or nothing."""

    problems: list[str] = []
    try:
        page.goto(page_url(base_url, path), wait_until="load", timeout=GOTO_TIMEOUT_MS)
        page.wait_for_selector(MAIN_SELECTOR, timeout=MAIN_TIMEOUT_MS)
    except PlaywrightTimeout as error:
        return [f"never loaded: {type(error).__name__}"]

    heading_rendered = True
    try:
        page.wait_for_function(HEADING_PRESENT_JS, arg=heading, timeout=HEADING_TIMEOUT_MS)
    except PlaywrightTimeout:
        heading_rendered = False

    # Collected whether or not the heading arrived. When a page fails at import time
    # the heading never renders, and the exception text is the only thing that says
    # why -- reporting the missing heading alone would describe the symptom and throw
    # away the cause.
    collected = page.evaluate(COLLECT_JS)

    for text in collected["exceptions"]:
        problems.append(f"raised: {text!r}")
    if not heading_rendered:
        problems.append(
            f"heading {heading!r} never rendered; main area began {collected['mainText']!r}"
        )
    return problems


def run(base_url: str, *, only: str | None = None) -> list[Finding]:
    pages = [(path, heading) for path, heading in PAGES if only is None or path == only]
    if not pages:
        raise SystemExit(f"no page matches {only!r}")

    findings: list[Finding] = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            for path, heading in pages:
                problems = inspect_page(page, base_url, path, heading)
                if problems:
                    # Retried once, and only once. See the module docstring: a cold
                    # container is slow, and a check that cries wolf is a check that
                    # stops being read.
                    problems = inspect_page(page, base_url, path, heading)
                if problems:
                    findings.extend(Finding(path, problem) for problem in problems)
                    print(f"FAIL {path} ({heading})")
                    for problem in problems:
                        print(f"     {problem}")
                else:
                    print(f"PASS {path} ({heading})")
            context.close()
        finally:
            browser.close()
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--only", help="check a single path, e.g. /trust")
    arguments = parser.parse_args()

    base_url = arguments.base_url.rstrip("/")
    print(f"Checking {base_url} ({len(PAGES)} pages)\n")
    findings = run(base_url, only=arguments.only)

    if findings:
        print(f"\n{len(findings)} problem(s) on the deployed app:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.path}: {finding.detail}", file=sys.stderr)
        raise SystemExit(1)
    print(f"\nEvery page rendered its own heading, and none raised, at {base_url}")


if __name__ == "__main__":
    main()
