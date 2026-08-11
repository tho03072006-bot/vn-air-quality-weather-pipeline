"""Browser-driven WCAG 2.1 AA text-contrast audit for every dashboard page.

The scanner resolves each direct HTML text node's foreground against the
background colours of its element and ancestors. A transparent ``body`` falls
back to ``html``; if both are transparent, the browser canvas is assumed white.
That explicit white-canvas assumption matches Chromium's default rendering but
should be revisited if the app adopts a non-colour background image or gradient.

Run it against an already-running app from the project root:

    streamlit run dashboard/app.py
    python scripts/verify_a11y.py

This is a reporting gate, intentionally separate from ``scripts/verify.ps1``.
It exits 1 when contrast findings are present and does not modify UI colours.
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

# ``python scripts/verify_a11y.py`` puts scripts/, not the project root, first.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.a11y import (  # noqa: E402, I001
    RGB,
    RGBA,
    composite,
    contrast_ratio,
    effective_background,
    is_icon_font,
    parse_css_colour,
    threshold_for,
    wcag_verdict,
)


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

VIEWPORTS = ((390, 844), (1280, 800))


AUDIT_JS = """
() => {
  const htmlNamespace = 'http://www.w3.org/1999/xhtml';
  const directText = (element) => [...element.childNodes]
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent || '')
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
  const hiddenBySelfOrAncestor = (element) => {
    for (let current = element; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden' ||
          style.visibility === 'collapse' || Number(style.opacity) === 0) {
        return true;
      }
    }
    return false;
  };

  const elements = [];
  document.querySelectorAll('[data-testid="stMain"] *').forEach((element) => {
    // SVG text uses different paint/compositing semantics and is audited by chart tooling.
    if (element.namespaceURI !== htmlNamespace) return;
    const text = directText(element);
    if (!text || hiddenBySelfOrAncestor(element)) return;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const style = getComputedStyle(element);
    const backgrounds = [];
    for (let current = element;
         current && current !== document.body && current !== document.documentElement;
         current = current.parentElement) {
      backgrounds.push(getComputedStyle(current).backgroundColor);
    }
    elements.push({
      text: text.slice(0, 60),
      colour: style.color,
      fontSize: Number.parseFloat(style.fontSize),
      fontWeight: style.fontWeight,
      fontFamily: style.fontFamily,
      backgrounds,
      testid: element.closest('[data-testid]')?.getAttribute('data-testid') || null,
      tag: element.tagName.toLowerCase(),
    });
  });

  return {
    bodyBackground: getComputedStyle(document.body).backgroundColor,
    htmlBackground: getComputedStyle(document.documentElement).backgroundColor,
    elements,
  };
}
"""


@dataclass(frozen=True, slots=True)
class Finding:
    page: str
    viewport: str
    detail: str


def click_main_button(page: Page, label: str) -> None:
    """Click a body button through JS while leaving the sidebar expanded."""

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


def _parsed_or_transparent(value: str) -> RGBA:
    return parse_css_colour(value) or (0, 0, 0, 0.0)


def _page_background(data: dict[str, Any]) -> RGB:
    return effective_background(
        [
            _parsed_or_transparent(data["bodyBackground"]),
            _parsed_or_transparent(data["htmlBackground"]),
        ],
        (255, 255, 255),
    )


def _font_weight(value: str) -> int:
    keyword_weights = {"normal": 400, "bold": 700, "bolder": 700, "lighter": 300}
    if value in keyword_weights:
        return keyword_weights[value]
    try:
        return int(float(value))
    except ValueError:
        return 400


def _rgb_text(colour: RGB) -> str:
    return f"rgb({colour[0]}, {colour[1]}, {colour[2]})"


def measure(page: Page, path: str, width: int, height: int) -> list[Finding]:
    data: dict[str, Any] = page.evaluate(AUDIT_JS)
    page_background = _page_background(data)
    viewport = f"{width}x{height}"
    findings: list[Finding] = []

    for element in data["elements"]:
        foreground = parse_css_colour(element["colour"])
        if foreground is None:
            findings.append(
                Finding(
                    path,
                    viewport,
                    f"unsupported foreground {element['colour']!r} for {element['text']!r}",
                )
            )
            continue

        layers = [_parsed_or_transparent(value) for value in element["backgrounds"]]
        background = effective_background(layers, page_background)
        rendered_foreground = composite(foreground, background)
        ratio = contrast_ratio(rendered_foreground, background)
        font_size = float(element["fontSize"])
        font_weight = _font_weight(str(element["fontWeight"]))
        font_family = str(element["fontFamily"])
        threshold = threshold_for(font_family, font_size, font_weight)
        criterion = "1.4.11" if is_icon_font(font_family) else "1.4.3"
        verdict = wcag_verdict(
            ratio,
            font_size,
            font_weight,
            font_family=font_family,
        )
        if verdict == "FAIL":
            context = element["testid"] or element["tag"]
            findings.append(
                Finding(
                    path,
                    viewport,
                    f"text={element['text']!r} ratio={ratio:.2f} threshold={threshold:.2f} "
                    f"criterion={criterion} verdict={verdict} "
                    f"foreground={_rgb_text(rendered_foreground)} "
                    f"background={_rgb_text(background)} size={font_size:g}px "
                    f"weight={font_weight} context={context}",
                )
            )
    return findings


def run(base_url: str) -> list[Finding]:
    findings: list[Finding] = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            for width, height in VIEWPORTS:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                for path, label in PAGES:
                    try:
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
                        print(f"FAIL {width}x{height} {path} ({label})")
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
    arguments = parser.parse_args()

    findings = run(arguments.base_url.rstrip("/"))
    if findings:
        print(f"\n{len(findings)} contrast problem(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.viewport} {finding.page}: {finding.detail}", file=sys.stderr)
        raise SystemExit(1)
    print("\nNo contrast problems at " + ", ".join(f"{w}x{h}" for w, h in VIEWPORTS))


if __name__ == "__main__":
    main()
