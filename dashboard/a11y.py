"""Pure helpers for evaluating text contrast under WCAG 2.1."""

import math
import re

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, float]

_HEX_COLOUR = re.compile(r"#[0-9a-f]+", re.IGNORECASE)
_FUNCTIONAL_COLOUR = re.compile(r"(rgba?)\s*\((.*)\)", re.IGNORECASE)
_INTEGER_TOKEN = re.compile(r"[+-]?\d+")
_NUMBER_TOKEN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def parse_css_colour(value: str) -> RGBA | None:
    """Parse a supported CSS colour string without raising for invalid input."""
    normalized = value.strip()
    if normalized.lower() == "transparent":
        return (0, 0, 0, 0.0)

    if _HEX_COLOUR.fullmatch(normalized):
        return _parse_hex_colour(normalized[1:])

    match = _FUNCTIONAL_COLOUR.fullmatch(normalized)
    if match is None:
        return None

    function_name, body = match.groups()
    parts = [part.strip() for part in body.split(",")] if "," in body else body.split()
    expected_parts = 4 if function_name.lower() == "rgba" else 3
    if len(parts) != expected_parts or any(not part for part in parts):
        return None

    channels = _parse_rgb_channels(parts[:3])
    if channels is None:
        return None

    alpha = 1.0
    if expected_parts == 4:
        alpha = _parse_alpha(parts[3])
        if alpha is None:
            return None

    return (*channels, alpha)


def _parse_hex_colour(value: str) -> RGBA | None:
    if len(value) in {3, 4}:
        value = "".join(character * 2 for character in value)
    if len(value) not in {6, 8}:
        return None

    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    alpha = int(value[6:8], 16) / 255 if len(value) == 8 else 1.0
    return (red, green, blue, alpha)


def _parse_rgb_channels(parts: list[str]) -> RGB | None:
    if any(_INTEGER_TOKEN.fullmatch(part) is None for part in parts):
        return None

    channels = tuple(int(part) for part in parts)
    if any(channel < 0 or channel > 255 for channel in channels):
        return None
    return channels[0], channels[1], channels[2]


def _parse_alpha(value: str) -> float | None:
    if _NUMBER_TOKEN.fullmatch(value) is None:
        return None

    alpha = float(value)
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        return None
    return alpha


def composite(foreground: RGBA, background: RGB) -> RGB:
    """Alpha-blend a foreground colour over an opaque background."""
    red, green, blue, alpha = foreground
    return (
        round(red * alpha + background[0] * (1.0 - alpha)),
        round(green * alpha + background[1] * (1.0 - alpha)),
        round(blue * alpha + background[2] * (1.0 - alpha)),
    )


def effective_background(layers: list[RGBA], page_background: RGB) -> RGB:
    """Composite inner-to-outer background layers onto an opaque page colour.

    Layers outside the first opaque layer cannot affect the result and are not
    evaluated further. Transparent layers are harmless, and an empty list simply
    exposes the page background.
    """

    relevant_layers: list[RGBA] = []
    for layer in layers:
        relevant_layers.append(layer)
        # Once an inner-to-outer scan reaches opaque paint, no farther
        # ancestor or page colour can contribute to the rendered background.
        if layer[3] >= 1.0:
            break

    background = page_background
    for layer in reversed(relevant_layers):
        background = composite(layer, background)
    return background


def relative_luminance(colour: RGB) -> float:
    """Return WCAG 2.1 relative luminance for an sRGB colour."""

    def linearize(channel: int) -> float:
        srgb = channel / 255
        if srgb <= 0.03928:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(channel) for channel in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: RGB, second: RGB) -> float:
    """Return the symmetric WCAG contrast ratio between two opaque colours."""
    first_luminance = relative_luminance(first)
    second_luminance = relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(font_size_px: float, font_weight: int) -> bool:
    """Return whether text meets the WCAG large-text size and weight rule."""
    return font_size_px >= 24 or (font_size_px >= 18.66 and font_weight >= 700)


def is_icon_font(font_family: str) -> bool:
    """Return whether a CSS font stack contains a Material Symbols icon font."""
    for family in font_family.split(","):
        normalized = family.strip().strip("\"'").casefold()
        if normalized == "material symbols" or normalized.startswith("material symbols "):
            return True
    return False


def required_ratio(*, large: bool) -> float:
    """Return the WCAG AA contrast threshold for the text size category."""
    return 3.0 if large else 4.5


def threshold_for(font_family: str, font_size_px: float, font_weight: int) -> float:
    """Return the WCAG threshold for non-text icons or size-classified text."""
    if is_icon_font(font_family):
        return 3.0
    return required_ratio(large=is_large_text(font_size_px, font_weight))


def wcag_verdict(
    ratio: float,
    font_size_px: float,
    font_weight: int,
    *,
    font_family: str = "",
) -> str:
    """Classify contrast against the applicable WCAG AA/AAA text or icon rule."""
    threshold = threshold_for(font_family, font_size_px, font_weight)
    if is_icon_font(font_family):
        # WCAG 1.4.11 is an AA non-text criterion and has no separate AAA tier.
        return "AA" if ratio >= threshold else "FAIL"

    large = is_large_text(font_size_px, font_weight)
    aaa_ratio = 4.5 if large else 7.0
    if ratio >= aaa_ratio:
        return "AAA"
    if ratio >= threshold:
        return "AA"
    return "FAIL"
