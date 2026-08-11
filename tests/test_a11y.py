import pytest

from dashboard.a11y import (
    composite,
    contrast_ratio,
    effective_background,
    is_icon_font,
    is_large_text,
    parse_css_colour,
    relative_luminance,
    required_ratio,
    threshold_for,
    wcag_verdict,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#0fA", (0, 255, 170, 1.0)),
        ("#0fA8", (0, 255, 170, 136 / 255)),
        ("#0F766E", (15, 118, 110, 1.0)),
        ("#0F766E80", (15, 118, 110, 128 / 255)),
    ],
)
def test_parse_css_colour_supports_all_hex_formats(
    value: str, expected: tuple[int, int, int, float]
) -> None:
    assert parse_css_colour(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" rgb(15, 118, 110) ", (15, 118, 110, 1.0)),
        ("RGB(15  118  110)", (15, 118, 110, 1.0)),
        (" RGBA ( 15, 118, 110, 0.25 ) ", (15, 118, 110, 0.25)),
        (" TrAnSpArEnT ", (0, 0, 0, 0.0)),
    ],
)
def test_parse_css_colour_supports_functional_and_keyword_formats(
    value: str, expected: tuple[int, int, int, float]
) -> None:
    assert parse_css_colour(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "#12",
        "#ggg",
        "rgb(256, 0, 0)",
        "rgb(0, 0)",
        "rgba(0, 0, 0, 1.1)",
        "hsl(0, 0%, 0%)",
        "not-a-colour",
    ],
)
def test_parse_css_colour_returns_none_for_invalid_input(value: str) -> None:
    assert parse_css_colour(value) is None


def test_composite_blends_half_transparent_black_over_white() -> None:
    assert composite((0, 0, 0, 0.5), (255, 255, 255)) == (128, 128, 128)


@pytest.mark.parametrize("background", [(0, 0, 0), (12, 34, 56), (255, 255, 255)])
def test_composite_opaque_foreground_replaces_background(
    background: tuple[int, int, int],
) -> None:
    assert composite((15, 118, 110, 1.0), background) == (15, 118, 110)


def test_composite_transparent_foreground_preserves_background() -> None:
    assert composite((15, 118, 110, 0.0), (248, 250, 252)) == (248, 250, 252)


def test_effective_background_returns_page_for_no_layers() -> None:
    assert effective_background([], (248, 250, 252)) == (248, 250, 252)


def test_effective_background_ignores_transparent_layers() -> None:
    assert effective_background(
        [(15, 118, 110, 0.0), (23, 32, 51, 0.0)],
        (248, 250, 252),
    ) == (248, 250, 252)


def test_effective_background_composites_two_half_black_layers_inner_to_outer() -> None:
    assert effective_background(
        [(0, 0, 0, 0.5), (0, 0, 0, 0.5)],
        (255, 255, 255),
    ) == (64, 64, 64)


def test_effective_background_inner_opaque_layer_hides_outer_opaque_layer() -> None:
    assert effective_background(
        [(255, 255, 255, 1.0), (0, 0, 0, 1.0)],
        (15, 118, 110),
    ) == (255, 255, 255)


def test_effective_background_stops_at_first_opaque_layer() -> None:
    assert effective_background(
        [
            (0, 0, 255, 0.5),
            (255, 255, 255, 1.0),
            (255, 0, 0, 1.0),
        ],
        (0, 0, 0),
    ) == (128, 128, 255)


def test_relative_luminance_has_black_and_white_endpoints() -> None:
    assert relative_luminance((0, 0, 0)) == 0.0
    assert relative_luminance((255, 255, 255)) == 1.0


@pytest.mark.parametrize(
    ("foreground", "background", "expected"),
    [
        ("#FFFFFF", "#000000", 21.00),
        ("#777777", "#FFFFFF", 4.48),
        ("#0F766E", "#F8FAFC", 5.23),
        ("#172033", "#F8FAFC", 15.55),
        ("#64748B", "#F8FAFC", 4.55),
        ("#94A3B8", "#F8FAFC", 2.45),
        ("#16A34A", "#FFFFFF", 3.30),
    ],
)
def test_contrast_ratio_matches_reference_values(
    foreground: str, background: str, expected: float
) -> None:
    assert contrast_ratio(_hex_rgb(foreground), _hex_rgb(background)) == pytest.approx(
        expected, abs=0.01
    )


@pytest.mark.parametrize(
    "colour",
    [(0, 0, 0), (15, 118, 110), (119, 119, 119), (248, 250, 252), (255, 255, 255)],
)
def test_contrast_ratio_of_identical_colours_is_one(colour: tuple[int, int, int]) -> None:
    assert contrast_ratio(colour, colour) == 1.0


def test_contrast_ratio_is_symmetric() -> None:
    first = (15, 118, 110)
    second = (248, 250, 252)
    assert contrast_ratio(first, second) == contrast_ratio(second, first)


@pytest.mark.parametrize(
    ("font_size_px", "font_weight", "expected"),
    [
        (24.0, 400, True),
        (23.9, 400, False),
        (18.66, 700, True),
        (18.65, 700, False),
        (18.66, 699, False),
        (18.66, 400, False),
    ],
)
def test_is_large_text_checks_both_sides_of_size_and_weight_thresholds(
    font_size_px: float, font_weight: int, expected: bool
) -> None:
    assert is_large_text(font_size_px, font_weight) is expected


def test_required_ratio_distinguishes_large_and_normal_text() -> None:
    assert required_ratio(large=True) == 3.0
    assert required_ratio(large=False) == 4.5


@pytest.mark.parametrize(
    "font_family",
    [
        '"Material Symbols Rounded"',
        "Material Symbols Outlined, sans-serif",
        "'Material Symbols Sharp', 'Source Sans', sans-serif",
    ],
)
def test_is_icon_font_accepts_material_symbols_in_css_font_stacks(font_family: str) -> None:
    assert is_icon_font(font_family) is True


@pytest.mark.parametrize(
    "font_family",
    ['"Source Sans", sans-serif', "Material Symbolic, sans-serif"],
)
def test_is_icon_font_rejects_regular_text_fonts(font_family: str) -> None:
    assert is_icon_font(font_family) is False


@pytest.mark.parametrize(
    ("font_family", "font_size_px", "font_weight", "expected"),
    [
        ('"Material Symbols Rounded"', 14.0, 400, 3.0),
        ('"Source Sans", sans-serif', 14.0, 400, 4.5),
        ('"Source Sans", sans-serif', 24.0, 400, 3.0),
    ],
)
def test_threshold_for_distinguishes_non_text_and_text_size(
    font_family: str,
    font_size_px: float,
    font_weight: int,
    expected: float,
) -> None:
    assert threshold_for(font_family, font_size_px, font_weight) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(4.49, "FAIL"), (4.5, "AA"), (6.99, "AA"), (7.0, "AAA")],
)
def test_wcag_verdict_checks_both_sides_of_normal_text_thresholds(
    ratio: float, expected: str
) -> None:
    assert wcag_verdict(ratio, 16.0, 400) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(2.99, "FAIL"), (3.0, "AA"), (4.49, "AA"), (4.5, "AAA")],
)
def test_wcag_verdict_checks_both_sides_of_large_text_thresholds(
    ratio: float, expected: str
) -> None:
    assert wcag_verdict(ratio, 24.0, 400) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(2.99, "FAIL"), (3.0, "AA")],
)
def test_wcag_verdict_checks_both_sides_of_non_text_threshold(
    ratio: float,
    expected: str,
) -> None:
    assert (
        wcag_verdict(
            ratio,
            14.0,
            400,
            font_family='"Material Symbols Rounded"',
        )
        == expected
    )


def _hex_rgb(value: str) -> tuple[int, int, int]:
    parsed = parse_css_colour(value)
    assert parsed is not None
    return parsed[0], parsed[1], parsed[2]
