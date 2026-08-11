"""Accessible HTML legend for exact map data colours."""

from __future__ import annotations

from html import escape

from dashboard.view_models import MISSING_RGB, MapMetric

MAP_LEGEND_PAGE_BACKGROUND = "#F8FAFC"
MAP_LEGEND_SWATCH_BORDER = "#475569"
MAP_LEGEND_TEXT = "#172033"


def _css_rgb(rgb: tuple[int, int, int]) -> str:
    return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


def map_legend_html(metric: MapMetric) -> str:
    """Return a wrapping legend whose swatches preserve the map's exact RGB values.

    Colour is data on this page, so the fill cannot be darkened to carry text. The
    label therefore sits beside an empty swatch in the normal page text colour. A
    dark two-pixel border supplies the non-text boundary contrast even when the fill
    itself is close to the page background.
    """

    entries = [(band.label, band.rgb) for band in metric.bands]
    entries.append(("Không có dữ liệu", MISSING_RGB))
    items = "".join(
        (
            '<span class="map-colour-legend__item" role="listitem">'
            '<span class="map-colour-legend__swatch" aria-hidden="true" '
            f'style="background-color: {_css_rgb(rgb)}"></span>'
            f'<span class="map-colour-legend__label">{escape(label)}</span>'
            "</span>"
        )
        for label, rgb in entries
    )
    aria_label = escape(f"Thang màu {metric.label}", quote=True)

    return f"""
    <style>
      .map-colour-legend {{
        align-items: center;
        color: {MAP_LEGEND_TEXT};
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem 1rem;
      }}

      .map-colour-legend__item {{
        align-items: center;
        display: inline-flex;
        gap: 0.4rem;
      }}

      .map-colour-legend__swatch {{
        border: 2px solid {MAP_LEGEND_SWATCH_BORDER};
        border-radius: 0.25rem;
        box-sizing: border-box;
        flex: 0 0 1rem;
        height: 1rem;
        width: 1rem;
      }}

      .map-colour-legend__label {{
        color: {MAP_LEGEND_TEXT};
        font-size: 0.875rem;
        line-height: 1.4;
      }}
    </style>
    <div class="map-colour-legend" role="list" aria-label="{aria_label}">{items}</div>
    """
