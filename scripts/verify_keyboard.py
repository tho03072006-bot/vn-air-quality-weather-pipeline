"""Browser-driven WCAG 2.1 AA keyboard audit for every dashboard page.

Three criteria, all measured rather than assumed:

* **2.1.1 Keyboard** -- every interactive element must be reachable by Tab.
* **2.4.7 Focus Visible** -- focusing an element must change how it looks.
* **2.4.3 Focus Order** -- Tab must walk the page in document order.

Run it against an already-running app from the project root:

    streamlit run dashboard/app.py
    python scripts/verify_keyboard.py

Like the contrast and layout gates this is a reporting gate, intentionally separate
from ``scripts/verify.ps1``, which is contractually offline. Exits 1 when findings
are present and never modifies the app.

``--skip-live-api`` drops the custom-location page, whose driver calls the real
forecast API. CI uses it to stay offline and deterministic; run without it locally
to cover all nine pages.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

# ``python scripts/verify_keyboard.py`` puts scripts/, not the project root, first.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.keyboard_a11y import (  # noqa: E402, I001
    FOCUS_INDICATOR_PROPERTIES,
    ElementSnapshot,
    FocusCycleDetector,
    FocusStop,
    LayoutBox,
    focus_indicator_visible,
    positive_tabindex_elements,
    reading_order_regressions,
    unreachable_interactive_elements,
)


PAGES: tuple[tuple[str, str], ...] = (
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

# The only page whose audit reaches an external service.
LIVE_API_PAGES: frozenset[str] = frozenset({"/custom_location"})

VIEWPORTS: tuple[tuple[int, int], ...] = ((390, 844), (1280, 800))

# Enough presses to cross the sidebar navigation and reach the page body.
TAB_PRESSES = 40

# Empty: all three criteria are enforced. 2.4.3 was advisory while its measurement
# still misreported, on this project's rule that a check which misreports is worse
# than no check. Seven wrong measurements had to be removed before it could be
# trusted, and the count is recorded because each one looked reasonable when written:
#
# * DOM index -- Streamlit portals paint at the top of the page while living at the
#   end of the document, so this reported jumps on six page/viewport pairs.
# * Running maximum -- after one genuine jump every later well-ordered step still
#   sits below the maximum, so one anomaly became five findings.
# * A fixed 40 Tab presses -- pages hold 16-29 stops, so the walk wrapped and
#   re-entered at the top: 23 of the 40 findings were that wrap.
# * Leaf position, compared flat -- a chart's toolbar and a grid's canvas share one
#   widget and neither orders the other. 24 findings.
# * Leaf position, compared across columns -- reading order in a column layout is
#   hierarchical: down the left column, then up to the top of the right. 11 findings.
# * Invisible elements included -- every heading carries an opacity-0 anchor link
#   that Tab lands on and no reader can see. 10 findings.
# * Container position measured during the walk -- focusing opens pickers and
#   scrolls the page; one container moved 439px between two presses. 3 findings.
#
# The last one is the sharpest: those three survived every other correction on a page
# whose DOM order was separately measured to match its visual order exactly. Reading
# order is a property of the page at rest, so container positions are now recorded
# once before the walk starts.
#
# Findings fell 37 -> 0 on the fixture warehouse without the gate losing its teeth:
# a CSS `order` that reverses two columns while leaving document order alone -- the
# textbook 2.4.3 violation -- still produces a finding on all eight pages.
ADVISORY_CRITERIA: frozenset[str] = frozenset()


COLLECT_JS = """
() => {
  const main = document.querySelector('[data-testid="stMain"]');
  if (!main) return [];
  const all = [...document.querySelectorAll('*')];
  return [...main.querySelectorAll('*')]
    .filter((el) => el.namespaceURI === 'http://www.w3.org/1999/xhtml')
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const hidden = style.display === 'none' || style.visibility === 'hidden';
      return {
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role'),
        href: el.getAttribute('href'),
        tabindex: el.getAttribute('tabindex'),
        disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
        ariaHidden: el.closest('[aria-hidden="true"]') !== null || hidden
          || (rect.width <= 0 && rect.height <= 0),
        label: (el.getAttribute('aria-label') || el.innerText || el.tagName)
          .replace(/\\s+/g, ' ').trim().slice(0, 40),
        documentIndex: all.indexOf(el),
        // Does the enclosing Streamlit widget expose the same function through some
        // element Tab CAN reach? A selectbox's dropdown-arrow button and a number
        // input's Increment/Decrement steppers are all tabindex=-1 beside a reachable
        // input that does the same job, so no function is withheld from a keyboard.
        //
        // Ancestors are walked rather than using `closest('[data-testid]')`, which
        // stops at the first testid: for a number input that is the little stepper
        // column holding only the two unreachable buttons, so the reachable input one
        // level further out was never seen and both steppers were reported.
        functionReachableInWidget: (() => {
          for (let widget = el.parentElement, depth = 0;
               widget && depth < 5;
               widget = widget.parentElement, depth += 1) {
            const reachable = [...widget.querySelectorAll(
              'button, a[href], input, select, textarea, [tabindex]'
            )].some((sibling) => sibling !== el
              && sibling.getAttribute('tabindex') !== '-1'
              && !sibling.hasAttribute('disabled'));
            if (reachable) return true;
          }
          return false;
        })(),
      };
    });
}
"""

# Baseline for every focusable element, captured BEFORE anything is focused, with a
# marker attribute so the Tab walk can match each focused element back to its own
# unfocused style.
#
# Focus must be reached by pressing Tab, not by calling `el.focus()`. Browsers apply
# `:focus-visible` -- which is what actually paints the ring -- on keyboard focus and
# frequently not on programmatic focus. Measuring with `el.focus()` reported ten of
# twelve controls on the History page as having no indicator when a real Tab showed a
# ring on all ten. The method was the defect, not the app.
TAG_AND_BASELINE_JS = """
(properties) => {
  const main = document.querySelector('[data-testid="stMain"]');
  if (!main) return {};
  const snapshot = (el) => {
    const style = getComputedStyle(el);
    return Object.fromEntries(properties.map((name) => [name, style[name]]));
  };
  const layersOf = (el) => {
    const layers = [];
    for (let c = el, depth = 0; c && depth < 4; c = c.parentElement, depth += 1) {
      layers.push(snapshot(c));
    }
    return layers;
  };

  const focusable = [...document.querySelectorAll(
    'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  )].filter((el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && !el.hasAttribute('disabled')
      && el.closest('[aria-hidden="true"]') === null;
  });
  focusable.forEach((el, index) => {
    el.setAttribute('data-kbcycle', String(index));
  });
  window.__kbcycleNext = focusable.length;

  const baseline = {};
  focusable.filter((el) => main.contains(el)).forEach((el, index) => {
    el.setAttribute('data-kbprobe', String(index));
    baseline[index] = {
      layers: layersOf(el),
      tag: el.tagName.toLowerCase(),
      label: (el.getAttribute('aria-label') || el.innerText || el.tagName)
        .replace(/\\s+/g, ' ').trim().slice(0, 40),
    };
  });
  // Where every layout container sits when the page is at rest. Reading order is a
  // property of the page a reader looks at, not of the page mid-interaction, and
  // measuring during the walk made it wrong: focusing a time input opens a picker,
  // focusing anything low on the page scrolls it, and either can move a container
  // hundreds of pixels between one Tab press and the next. Three findings survived
  // every other correction purely because of that, on a page whose DOM order was
  // measured to match its visual order exactly.
  const LAYOUT_TESTIDS = ['stHorizontalBlock', 'stColumn', 'stLayoutWrapper',
                          'stVerticalBlock', 'stElementContainer'];
  const containers = {};
  [...main.querySelectorAll('[data-testid]')].forEach((node, index) => {
    const testid = node.getAttribute('data-testid');
    if (LAYOUT_TESTIDS.indexOf(testid) === -1) return;
    const key = `container-${index}`;
    node.setAttribute('data-kbcontainer', key);
    const box = node.getBoundingClientRect();
    containers[key] = {
      y: box.top + window.scrollY, x: box.left + window.scrollX,
      width: box.width, height: box.height,
    };
  });
  return {baseline, containers};
}
"""

FOCUSED_STATE_JS = """
(properties) => {
  const el = document.activeElement;
  if (!el) return null;
  const isBody = el === document.body;
  if (isBody) {
    return {
      cycleId: null,
      probeIndex: null,
      isBody: true,
      inMain: false,
      anchored: false,
      layers: [],
    };
  }
  let cycleId = el.getAttribute('data-kbcycle');
  if (cycleId === null) {
    cycleId = `dynamic-${window.__kbcycleNext ?? 0}`;
    window.__kbcycleNext = (window.__kbcycleNext ?? 0) + 1;
    el.setAttribute('data-kbcycle', cycleId);
  }
  const snapshot = (node) => {
    const style = getComputedStyle(node);
    return Object.fromEntries(properties.map((name) => [name, style[name]]));
  };
  const layers = [];
  for (let c = el, depth = 0; c && depth < 4; c = c.parentElement, depth += 1) {
    layers.push(snapshot(c));
  }
  // Document-absolute pixels, so the sequence survives the scrolling that focusing
  // causes. 2.4.3 is judged against what the reader sees, not against DOM order.
  const rect = el.getBoundingClientRect();
  // A fixed or sticky element is anchored to the viewport rather than sitting in the
  // page's flow, so its coordinates cannot be compared with flowed content. Streamlit
  // pins its toolbar and header controls that way, and including them reported a
  // backwards jump to y=0 on every page that has one.
  let anchored = false;
  for (let c = el; c; c = c.parentElement) {
    const position = getComputedStyle(c).position;
    if (position === 'fixed' || position === 'sticky') { anchored = true; break; }
  }
  // Whether a reader can actually see this element. Streamlit gives every heading a
  // "Link to heading" anchor that is a real <a href> -- so Tab lands on it -- painted
  // at opacity 0 until the heading is hovered. Nine of them sit at scattered
  // positions on a page, and judging a reading order against something invisible is
  // judging noise. Position is only meaningful for what is on screen.
  let visible = rect.width > 0 && rect.height > 0;
  for (let c = el; c && visible; c = c.parentElement) {
    const s = getComputedStyle(c);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) {
      visible = false;
    }
  }
  // Every layout container from the main region down to the element, outermost
  // first. Reading order in a column layout is hierarchical, so two stops have to
  // be compared at the outermost container where their paths diverge -- see
  // reading_order_regressions. Each container is tagged so "still inside this one"
  // can be told from "came back to it", which are not the same thing.
  // Only the identity of each container on the path. Their positions were recorded
  // once, before the walk started, by TAG_AND_BASELINE_JS -- see the comment there.
  const path = [];
  for (let c = el; c && c !== document.body; c = c.parentElement) {
    const key = c.getAttribute('data-kbcontainer');
    if (key !== null) path.push(key);
  }
  path.reverse();
  return {
    cycleId,
    probeIndex: el.getAttribute('data-kbprobe'),
    isBody: false,
    y: rect.top + window.scrollY,
    x: rect.left + window.scrollX,
    path,
    visible,
    anchored,
    inMain: el.closest('[data-testid="stMain"]') !== null,
    layers,
  };
}
"""


@dataclass(frozen=True, slots=True)
class Finding:
    page: str
    viewport: str
    criterion: str
    detail: str


def click_main_button(page: Page, label: str) -> None:
    """Click a body button through JS while leaving the sidebar expanded.

    Collapsing the sidebar would widen the main column and change the very layout
    the sibling gates measure, so the click goes through JS instead.
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
    click_main_button(page, "Áp dụng")
    page.wait_for_timeout(4000)


def drive_custom_location(page: Page) -> None:
    click_main_button(page, "Nhập tọa độ")
    page.wait_for_timeout(2500)
    click_main_button(page, "Dùng tọa độ này")
    page.wait_for_timeout(14000)


DRIVERS = {"/history": drive_history, "/custom_location": drive_custom_location}


def _snapshots(page: Page) -> list[ElementSnapshot]:
    raw: list[dict[str, Any]] = page.evaluate(COLLECT_JS)
    return [
        ElementSnapshot(
            tag=str(item["tag"]),
            role=item["role"],
            href=item["href"],
            tabindex=item["tabindex"],
            disabled=bool(item["disabled"]),
            aria_hidden=bool(item["ariaHidden"]),
            label=str(item["label"]),
            function_reachable_in_widget=bool(item["functionReachableInWidget"]),
        )
        for item in raw
    ]


def _walk_with_tab(
    page: Page,
) -> tuple[list[FocusStop], list[dict[str, Any]]]:
    """Press Tab across the page, collecting focus order and focus appearance at once.

    Returns `(stops, unfocused_findings)`. One walk serves both criteria because both
    need the same thing: the element the keyboard actually landed on.
    """

    tagged: dict[str, Any] = page.evaluate(TAG_AND_BASELINE_JS, list(FOCUS_INDICATOR_PROPERTIES))
    baseline: dict[str, Any] = tagged["baseline"]
    containers: dict[str, Any] = tagged["containers"]
    page.evaluate("() => document.body.focus()")

    stops: list[FocusStop] = []
    without_indicator: list[dict[str, Any]] = []
    judged: set[str] = set()
    cycle_detector = FocusCycleDetector()

    for _ in range(TAB_PRESSES):
        page.keyboard.press("Tab")
        step = page.evaluate(FOCUSED_STATE_JS, list(FOCUS_INDICATOR_PROPERTIES))
        if step is None:
            continue
        if cycle_detector.should_stop(
            step["cycleId"],
            in_main=bool(step["inMain"]),
            is_body=bool(step["isBody"]),
        ):
            break
        if step["isBody"] or not step["inMain"]:
            # Only the main region is judged; entering it from the sidebar is one
            # legitimate jump that would otherwise read as a regression.
            continue
        # Focus appearance is still judged for anchored and invisible controls; only
        # their place in the reading order is meaningless.
        if not step["anchored"] and step["visible"]:
            stops.append(
                FocusStop(
                    path=tuple(
                        LayoutBox(
                            key=str(key),
                            y=float(containers[key]["y"]),
                            x=float(containers[key]["x"]),
                            width=float(containers[key]["width"]),
                            height=float(containers[key]["height"]),
                        )
                        for key in step["path"]
                        if key in containers
                    )
                )
            )

        probe = step["probeIndex"]
        if probe is None or probe in judged or probe not in baseline:
            continue
        judged.add(probe)
        base = baseline[probe]
        if not focus_indicator_visible(base["layers"], step["layers"]):
            without_indicator.append(base)

    return stops, without_indicator


def measure(page: Page, path: str, viewport: str) -> list[Finding]:
    findings: list[Finding] = []
    elements = _snapshots(page)

    for element in unreachable_interactive_elements(elements):
        findings.append(
            Finding(
                path,
                viewport,
                "2.1.1",
                f"interactive <{element.tag}> {element.label!r} has tabindex="
                f"{element.tabindex!r}, so Tab cannot reach it",
            )
        )

    for element in positive_tabindex_elements(elements):
        findings.append(
            Finding(
                path,
                viewport,
                "2.4.3",
                f"<{element.tag}> {element.label!r} has tabindex={element.tabindex!r}; a "
                "positive value jumps ahead of every naturally ordered control",
            )
        )

    stops, without_indicator = _walk_with_tab(page)

    for step, (y, x) in reading_order_regressions(stops):
        findings.append(
            Finding(
                path,
                viewport,
                "2.4.3",
                f"Tab moved backwards on screen at layout container {step}, to y={y:.0f} x={x:.0f}",
            )
        )

    for observed in without_indicator:
        findings.append(
            Finding(
                path,
                viewport,
                "2.4.7",
                f"<{observed['tag']}> {observed['label']!r} looks identical focused "
                "and unfocused under a real Tab press",
            )
        )
    return findings


def run(base_url: str, *, skip_live_api: bool) -> list[Finding]:
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
                viewport = f"{width}x{height}"
                for path, label in pages:
                    try:
                        page.goto(f"{base_url}{path}", wait_until="load", timeout=45000)
                        page.wait_for_selector('[data-testid="stMain"]', timeout=30000)
                        page.wait_for_timeout(3000)
                        driver_fn = DRIVERS.get(path)
                        if driver_fn is not None:
                            driver_fn(page)
                    except (PlaywrightTimeout, LookupError) as error:
                        findings.append(Finding(path, viewport, "n/a", f"never settled: {error}"))
                        print(f"FAIL {viewport} {path} ({label})")
                        continue

                    page_findings = measure(page, path, viewport)
                    findings.extend(page_findings)
                    blocking = [
                        finding
                        for finding in page_findings
                        if finding.criterion not in ADVISORY_CRITERIA
                    ]
                    status = "FAIL" if blocking else "PASS"
                    advisory = len(page_findings) - len(blocking)
                    note = f"  ({advisory} advisory)" if advisory else ""
                    print(f"{status} {viewport} {path} ({label}){note}")
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
    blocking = [f for f in findings if f.criterion not in ADVISORY_CRITERIA]
    advisory = [f for f in findings if f.criterion in ADVISORY_CRITERIA]

    def report(items: list[Finding], stream: Any) -> None:
        for finding in items:
            print(
                f"  {finding.viewport} {finding.page} [{finding.criterion}]: {finding.detail}",
                file=stream,
            )

    if advisory:
        print(
            f"\n{len(advisory)} advisory finding(s) -- reported, not enforced; "
            f"see ADVISORY_CRITERIA for why:"
        )
        report(advisory, sys.stdout)

    if blocking:
        print(f"\n{len(blocking)} keyboard problem(s):", file=sys.stderr)
        report(blocking, sys.stderr)
        raise SystemExit(1)

    scope = " (live-API pages skipped)" if arguments.skip_live_api else ""
    print(
        "\nNo blocking keyboard problems at " + ", ".join(f"{w}x{h}" for w, h in VIEWPORTS) + scope
    )


if __name__ == "__main__":
    main()
