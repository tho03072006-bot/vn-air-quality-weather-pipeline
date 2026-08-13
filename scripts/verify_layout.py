"""Browser layout checks for the two defect classes AppTest structurally cannot see.

`verify_streamlit.py` runs on AppTest, which has no DOM and no layout engine. It
asserts that text and widgets exist, and it has never been able to see:

1. a chart drawn wider than the container it sits in;
2. a value clipped by CSS, which is how "49.8 µg/m³" once rendered as "75…".

Both shipped. Both were found by eye. The first recurred: it was fixed at desktop
width, and a later measurement found the same faceted grid at 641px inside a 327px
column on a 390px viewport, where no ancestor scrolled, so three of six pollutant
panels were simply unreachable. A defect class that has recurred after a fix is the
argument for a check rather than another look.

Run it against a server you have already started:

    streamlit run dashboard/app.py          # from the project root
    python scripts/verify_layout.py

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

# Path, and whether reaching the interesting state needs the page driven first.
PAGES = (
    ("/", "Hôm nay"),
    ("/forecast", "Dự báo"),
    ("/custom_location", "Địa điểm tùy chọn"),
    ("/national_map", "Bản đồ"),
    ("/compare", "So sánh"),
    ("/history", "Lịch sử"),
    ("/alerts", "Cảnh báo"),
    ("/trust", "Độ tin cậy"),
    ("/pipeline_health", "Pipeline health"),
)

# 390x844 is the iPhone-class breakpoint the facet grid failed at; 1280x800 is the
# width every earlier fix was checked against. Both, because the first fix passed at
# one and failed at the other.
# The only page whose audit reaches an external service: its driver enters
# coordinates, which calls the live forecast API. CI drops it to stay offline.
LIVE_API_PAGES: frozenset[str] = frozenset({"/custom_location"})

VIEWPORTS = ((390, 844), (1280, 800))

# Measured in the browser: every non-faceted chart reports svg width exactly equal to
# its container, so anything above a rounding pixel is a real overflow.
OVERFLOW_TOLERANCE_PX = 1

# Only HTML elements. An SVG <text> node reports clientWidth 9 against scrollWidth 30
# purely because SVG does not use the box model, so including them made the check
# fire on every correct chart -- a check that cannot pass on correct code is worse
# than no check, so the namespace filter is load-bearing.
MEASURE_JS = """
() => {
  const result = {charts: [], clipped: [], horizontalScroll:
    document.documentElement.scrollWidth > document.documentElement.clientWidth + 1};
  document.querySelectorAll('[data-testid="stVegaLiteChart"]').forEach((chart, index) => {
    const parent = chart.parentElement;
    const svg = chart.querySelector('svg');
    const drawn = svg ? svg.getBoundingClientRect().width : chart.scrollWidth;
    result.charts.push({
      index,
      drawn: Math.round(drawn),
      container: parent.clientWidth,
      overflow: Math.round(drawn - parent.clientWidth),
    });
  });
  document.querySelectorAll('[data-testid="stMain"] *').forEach((el) => {
    if (el.namespaceURI !== 'http://www.w3.org/1999/xhtml') return;
    if (el.children.length) return;
    const text = (el.textContent || '').trim();
    if (!text) return;
    if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1) {
      result.clipped.push({
        text: text.slice(0, 60),
        testid: el.closest('[data-testid]')?.getAttribute('data-testid') || null,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      });
    }
  });
  return result;
}
"""


@dataclass(frozen=True, slots=True)
class Finding:
    page: str
    viewport: str
    detail: str


def click_main_button(page: Page, label: str) -> None:
    """Click a body button by its label, through JS.

    Deliberately not `locator.click()`. With the sidebar in its default expanded
    state Playwright refuses the click -- the sidebar subtree "intercepts pointer
    events" -- and the first version of this script worked around that by collapsing
    the sidebar first. That was a mistake worth recording: a collapsed sidebar widens
    the main column, and the three clipped metric labels this script exists to catch
    stopped being clipped, so the workaround silently turned a failing run green.
    The sidebar stays as a reader finds it and the click goes through JS instead.
    """

    clicked = page.evaluate(
        """(label) => {
          const main = document.querySelector('[data-testid="stMain"]');
          if (!main) return false;
          const button = [...main.querySelectorAll('button')]
            .find((candidate) => candidate.innerText.includes(label));
          if (!button) return false;
          button.click();
          return true;
        }""",
        label,
    )
    if not clicked:
        raise LookupError(f"no button labelled {label!r} in the page body")


def drive_history(page: Page) -> None:
    """Submit the filter form; everything worth measuring is behind it."""

    click_main_button(page, "Áp dụng")
    page.wait_for_timeout(4000)


def drive_custom_location(page: Page) -> None:
    """Switch to coordinate entry and submit, avoiding the geocoding round trip.

    Still calls the public forecast API, which is what the page does for a reader.
    """

    click_main_button(page, "Nhập tọa độ")
    page.wait_for_timeout(2500)
    click_main_button(page, "Dùng tọa độ này")
    page.wait_for_timeout(14000)


DRIVERS = {"/history": drive_history, "/custom_location": drive_custom_location}


def measure(page: Page, path: str, width: int, height: int) -> list[Finding]:
    findings: list[Finding] = []
    viewport = f"{width}x{height}"
    data = page.evaluate(MEASURE_JS)

    for chart in data["charts"]:
        if chart["overflow"] > OVERFLOW_TOLERANCE_PX:
            findings.append(
                Finding(
                    path,
                    viewport,
                    f"chart #{chart['index']} drawn {chart['drawn']}px inside a "
                    f"{chart['container']}px container (+{chart['overflow']}px)",
                )
            )
    for clip in data["clipped"]:
        findings.append(
            Finding(
                path,
                viewport,
                f"text clipped by CSS: {clip['text']!r} needs {clip['scrollWidth']}px "
                f"in {clip['clientWidth']}px ({clip['testid']})",
            )
        )
    if data["horizontalScroll"]:
        findings.append(Finding(path, viewport, "the page itself scrolls horizontally"))
    return findings


def run(base_url: str, *, skip_live_api: bool = False) -> list[Finding]:
    pages = [
        (path, label) for path, label in PAGES if not (skip_live_api and path in LIVE_API_PAGES)
    ]
    findings: list[Finding] = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            for width, height in VIEWPORTS:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                for path, label in pages:
                    try:
                        # Not networkidle: the map page holds a connection open for
                        # its tiles and the websocket never goes quiet, so waiting
                        # for idle timed out on a page that had rendered fine.
                        page.goto(f"{base_url}{path}", wait_until="load", timeout=45000)
                        page.wait_for_selector('[data-testid="stMain"]', timeout=30000)
                        page.wait_for_timeout(3000)
                        driver_fn = DRIVERS.get(path)
                        if driver_fn is not None:
                            driver_fn(page)
                    except (PlaywrightTimeout, LookupError) as error:
                        findings.append(
                            Finding(path, f"{width}x{height}", f"never settled: {error}")
                        )
                        continue
                    page_findings = measure(page, path, width, height)
                    findings.extend(page_findings)
                    status = "FAIL" if page_findings else "PASS"
                    print(f"{status} {width}x{height} {path} ({label})")
                context.close()
        finally:
            browser.close()
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8501")
    parser.add_argument(
        "--skip-live-api",
        action="store_true",
        help="drop pages whose audit calls an external API (CI uses this)",
    )
    arguments = parser.parse_args()

    findings = run(arguments.base_url.rstrip("/"), skip_live_api=arguments.skip_live_api)
    if findings:
        print(f"\n{len(findings)} layout problem(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.viewport} {finding.page}: {finding.detail}", file=sys.stderr)
        raise SystemExit(1)
    scope = " (live-API pages skipped)" if arguments.skip_live_api else ""
    print("\nNo layout problems at " + ", ".join(f"{w}x{h}" for w, h in VIEWPORTS) + scope)


if __name__ == "__main__":
    main()
