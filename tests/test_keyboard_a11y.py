"""Keyboard accessibility rules, tested away from the browser.

The rules decide what counts as a WCAG 2.1.1 / 2.4.3 / 2.4.7 failure. Getting them
wrong in either direction is expensive: too strict and the gate reports Streamlit's
decorative wrappers as violations, too loose and it passes a mouse-only control.
"""

import pytest

from dashboard.keyboard_a11y import (
    ElementSnapshot,
    FocusCycleDetector,
    FocusStop,
    LayoutBox,
    focus_indicator_changed,
    focus_indicator_visible,
    focus_order_regressions,
    is_interactive,
    is_keyboard_reachable,
    positive_tabindex_elements,
    reading_order_regressions,
    unreachable_interactive_elements,
)


def snapshot(
    tag: str = "div",
    *,
    role: str | None = None,
    href: str | None = None,
    tabindex: str | None = None,
    disabled: bool = False,
    aria_hidden: bool = False,
    label: str = "x",
    function_reachable_in_widget: bool = False,
) -> ElementSnapshot:
    return ElementSnapshot(
        tag=tag,
        role=role,
        href=href,
        tabindex=tabindex,
        disabled=disabled,
        aria_hidden=aria_hidden,
        label=label,
        function_reachable_in_widget=function_reachable_in_widget,
    )


@pytest.mark.parametrize("tag", ["button", "select", "textarea", "input", "summary"])
def test_natively_interactive_tags_are_interactive(tag: str) -> None:
    assert is_interactive(snapshot(tag)) is True


def test_anchor_is_interactive_only_with_an_href() -> None:
    # `<a>` without href is a styling hook, not a control. Treating it as interactive
    # would report a violation on every decorative anchor Streamlit renders.
    assert is_interactive(snapshot("a", href="/forecast")) is True
    assert is_interactive(snapshot("a")) is False


@pytest.mark.parametrize("role", ["button", "checkbox", "combobox", "slider", "tab"])
def test_aria_roles_make_a_plain_div_interactive(role: str) -> None:
    assert is_interactive(snapshot("div", role=role)) is True


def test_decorative_container_is_not_interactive() -> None:
    assert is_interactive(snapshot("div")) is False
    assert is_interactive(snapshot("span", role="presentation")) is False


def test_disabled_and_hidden_controls_owe_no_tab_stop() -> None:
    # A disabled control is operable by no input method, and an aria-hidden one is
    # not exposed at all, so neither is a 2.1.1 failure.
    assert is_interactive(snapshot("button", disabled=True)) is False
    assert is_interactive(snapshot("button", aria_hidden=True)) is False


def test_author_supplied_tabindex_claims_focusability() -> None:
    assert is_interactive(snapshot("div", tabindex="0")) is True


def test_negative_tabindex_is_not_reachable_by_tab() -> None:
    assert is_keyboard_reachable(snapshot("button", tabindex="-1")) is False
    assert is_keyboard_reachable(snapshot("button", tabindex="0")) is True
    assert is_keyboard_reachable(snapshot("button")) is True


def test_unparseable_tabindex_is_treated_as_absent() -> None:
    # Browsers ignore a non-numeric tabindex and leave the element in its natural
    # position; the rule has to agree or it reports a phantom violation.
    element = snapshot("button", tabindex="banana")
    assert is_keyboard_reachable(element) is True
    assert positive_tabindex_elements([element]) == []


def test_mouse_only_button_is_reported() -> None:
    reachable = snapshot("button", label="Áp dụng")
    mouse_only = snapshot("button", tabindex="-1", label="Làm mới")

    found = unreachable_interactive_elements([reachable, mouse_only])

    assert [element.label for element in found] == ["Làm mới"]


def test_unreachable_element_is_forgiven_when_its_widget_stays_operable() -> None:
    """Streamlit's selectbox arrow, which the first browser run flagged on every page.

    2.1.1 requires the *function* to be keyboard-operable. The arrow is
    `tabindex="-1"` but sits beside a reachable combobox input that opens the same
    menu, so nothing is withheld from a keyboard user.
    """

    arrow = snapshot("button", tabindex="-1", label="Open", function_reachable_in_widget=True)

    assert unreachable_interactive_elements([arrow]) == []


def test_widget_flag_does_not_excuse_a_genuinely_trapped_control() -> None:
    # Same shape, but nothing else in the widget takes focus: the function really is
    # unreachable and must still be reported.
    trapped = snapshot("button", tabindex="-1", label="Xoá", function_reachable_in_widget=False)

    assert [e.label for e in unreachable_interactive_elements([trapped])] == ["Xoá"]


def test_hidden_element_with_negative_tabindex_is_not_a_violation() -> None:
    # Streamlit parks inactive widgets at tabindex=-1 while aria-hidden. Reporting
    # those would bury the real findings in noise.
    parked = snapshot("button", tabindex="-1", aria_hidden=True)

    assert unreachable_interactive_elements([parked]) == []


def test_positive_tabindex_is_flagged_but_zero_is_not() -> None:
    # A positive tabindex jumps ahead of every naturally ordered control on the page,
    # so one of them breaks the order for everything else.
    elements = [
        snapshot("button", tabindex="0", label="natural"),
        snapshot("button", tabindex="3", label="jumps the queue"),
        snapshot("button", label="no tabindex"),
    ]

    assert [element.label for element in positive_tabindex_elements(elements)] == [
        "jumps the queue"
    ]


def test_focus_indicator_detects_an_outline_appearing() -> None:
    before = {"outlineStyle": "none", "outlineWidth": "0px", "boxShadow": "none"}
    after = {"outlineStyle": "solid", "outlineWidth": "2px", "boxShadow": "none"}

    assert focus_indicator_changed(before, after) is True


def test_focus_indicator_absent_when_nothing_visible_changes() -> None:
    style = {"outlineStyle": "none", "outlineWidth": "0px", "boxShadow": "none"}

    assert focus_indicator_changed(style, dict(style)) is False


def test_focus_indicator_ignores_properties_outside_the_compared_set() -> None:
    # Layout metrics settle after focus scrolls an element into view. Counting those
    # as an indicator would make every element pass, including one with
    # `outline: none`, which is the exact defect this criterion exists to catch.
    before = {"outlineStyle": "none", "width": "100px"}
    after = {"outlineStyle": "none", "width": "220px"}

    assert focus_indicator_changed(before, after) is False


def test_focus_ring_on_the_wrapper_counts_as_visible() -> None:
    """The false positive the first browser run produced on every text input.

    Streamlit paints the ring on the wrapper `<div>`, not on the `<input>` that holds
    DOM focus. Judging the focused node alone called every text field and selectbox a
    2.4.7 failure; a reader sees the widget, not the node.
    """

    unchanged = {"outlineStyle": "none", "boxShadow": "none"}
    wrapper_before = {"outlineStyle": "none", "boxShadow": "none"}
    wrapper_after = {"outlineStyle": "none", "boxShadow": "0 0 0 2px #0F766E"}

    assert (
        focus_indicator_visible([unchanged, wrapper_before], [dict(unchanged), wrapper_after])
        is True
    )


def test_no_indicator_anywhere_in_the_stack_is_still_a_failure() -> None:
    # The check must stay able to fail, or it is worse than no check: this is the
    # `outline: none` defect 2.4.7 exists to catch.
    flat = {"outlineStyle": "none", "boxShadow": "none"}
    layers = [flat, dict(flat), dict(flat)]

    assert focus_indicator_visible(layers, [dict(layer) for layer in layers]) is False


def test_focus_order_accepts_a_sequence_that_moves_down_the_page() -> None:
    assert focus_order_regressions([(0.0, 10.0), (40.0, 10.0), (90.0, 10.0)]) == []


def test_focus_order_accepts_a_row_traversed_left_to_right() -> None:
    # Three buttons side by side on one row: same y, increasing x.
    row = [(120.0, 10.0), (120.0, 90.0), (124.0, 170.0)]

    assert focus_order_regressions(row) == []


def test_focus_order_reports_focus_jumping_back_up_the_page() -> None:
    regressions = focus_order_regressions([(0.0, 10.0), (400.0, 10.0), (120.0, 10.0)])

    assert regressions == [(2, (120.0, 10.0))]


def test_focus_order_reports_moving_leftwards_within_one_row() -> None:
    regressions = focus_order_regressions([(120.0, 200.0), (120.0, 40.0)])

    assert regressions == [(1, (120.0, 40.0))]


def test_portal_rendered_control_painted_at_the_top_is_not_a_regression() -> None:
    """The false positive that DOM-index comparison produced on six combinations.

    Streamlit appends popovers to the end of the DOM but paints them at the top of the
    page. By document index that reads as a huge backwards jump; on screen the reader
    simply sees focus start at the top and move down, which is correct.
    """

    seen_by_reader = [(24.0, 10.0), (72.0, 10.0), (130.0, 10.0), (190.0, 10.0)]

    assert focus_order_regressions(seen_by_reader) == []


def test_one_early_jump_reports_one_finding_not_a_cascade() -> None:
    """A running-maximum comparison turned a single anomaly into four findings."""

    regressions = focus_order_regressions(
        [(400.0, 10.0), (100.0, 10.0), (150.0, 10.0), (200.0, 10.0)]
    )

    assert regressions == [(1, (100.0, 10.0))]


def test_focus_order_tolerates_repeated_positions() -> None:
    # A composite widget can keep focus in one place across two Tab presses.
    assert focus_order_regressions([(50.0, 10.0), (50.0, 10.0), (80.0, 10.0)]) == []


def test_focus_order_handles_an_empty_sequence() -> None:
    assert focus_order_regressions([]) == []


def test_repeated_focus_element_limits_the_walk_to_one_cycle() -> None:
    detector = FocusCycleDetector()
    judged: list[str] = []

    for focus_id in ["sidebar-home", "main-scroll", "main-filter", "sidebar-home"]:
        if detector.should_stop(
            focus_id,
            in_main=focus_id.startswith("main-"),
            is_body=False,
        ):
            break
        judged.append(focus_id)

    assert judged == ["sidebar-home", "main-scroll", "main-filter"]


def test_body_ends_the_walk_only_after_focus_has_entered_main() -> None:
    detector = FocusCycleDetector()

    assert detector.should_stop(None, in_main=False, is_body=True) is False
    assert detector.should_stop("sidebar-home", in_main=False, is_body=False) is False
    assert detector.should_stop("main-filter", in_main=True, is_body=False) is False
    assert detector.should_stop(None, in_main=False, is_body=True) is True


# --- WCAG 2.4.3 reading order ------------------------------------------------
#
# Every case below is a shape that was measured on the real dashboard and got
# reported as a defect by an earlier version of this rule. Coordinates are the
# measured ones, so a regression here is a regression against the real page.

ROW = LayoutBox("row", y=100, x=0, width=1000, height=400)


def _stop(*boxes: LayoutBox) -> FocusStop:
    return FocusStop(path=tuple(boxes))


def test_moving_from_the_left_column_to_the_right_column_is_not_a_regression() -> None:
    """Down the left column, then up to the top of the right one, is correct order.

    The measured case: /history at 1280 puts the filter form in the left column and
    a chart in the right. Tab leaves the submit button at y=752 and lands on the
    chart at y=389 -- 363px *up* the page, and perfectly sensible to a reader.
    """

    left = LayoutBox("left", y=100, x=0, width=500, height=400)
    right = LayoutBox("right", y=100, x=500, width=500, height=400)

    stops = [
        _stop(ROW, left, LayoutBox("submit", y=452, x=0, width=200, height=40)),
        _stop(ROW, right, LayoutBox("chart", y=110, x=500, width=400, height=200)),
    ]

    assert reading_order_regressions(stops) == []


def test_reversing_two_columns_visually_is_a_regression() -> None:
    """A CSS `order` swap leaves document order alone and reverses what is read.

    This is the mutation the gate is proven against: without it the rule could be
    satisfied by comparing document order, which cannot see this at all.
    """

    visually_right = LayoutBox("first-in-dom", y=100, x=500, width=500, height=400)
    visually_left = LayoutBox("second-in-dom", y=100, x=0, width=500, height=400)

    stops = [
        _stop(ROW, visually_right, LayoutBox("a", y=110, x=500, width=100, height=40)),
        _stop(ROW, visually_left, LayoutBox("b", y=110, x=0, width=100, height=40)),
    ]

    regressions = reading_order_regressions(stops)
    assert len(regressions) == 1
    assert regressions[0][0] == 1


def test_two_stops_inside_one_widget_are_not_compared() -> None:
    """A chart's toolbar sits at its own top right; the canvas sits below and left.

    Measured on /pipeline_health: toolbar at x=1106, grid canvas at x=381, same
    widget. Comparing them reported a 793px jump left. The widget owns its own
    internal order, and this project does not author it.
    """

    widget = LayoutBox("widget", y=380, x=380, width=820, height=220)
    stops = [
        _stop(ROW, widget, LayoutBox("toolbar", y=389, x=1106, width=90, height=24)),
        _stop(ROW, widget, LayoutBox("canvas", y=504, x=381, width=800, height=100)),
    ]

    assert reading_order_regressions(stops) == []


def test_a_container_drawn_inside_another_is_layered_not_sequenced() -> None:
    """The map draws its layer picker on top of itself.

    Measured on /national_map at 1280: the map container is 820x620 at y=180 and the
    picker is 495x60 at y=488, inside it. They are DOM siblings that overlap on
    screen, so there is no before-and-after between them to get wrong.
    """

    picker = LayoutBox("picker", y=488, x=380, width=495, height=60)
    map_box = LayoutBox("map", y=180, x=380, width=820, height=620)

    stops = [
        _stop(ROW, picker, LayoutBox("radio", y=490, x=390, width=100, height=30)),
        _stop(ROW, map_box, LayoutBox("canvas", y=200, x=380, width=820, height=600)),
    ]

    assert reading_order_regressions(stops) == []


def test_returning_to_an_earlier_container_is_a_regression() -> None:
    """Leaving a container and coming back to it is a real backwards jump."""

    first = LayoutBox("first", y=100, x=0, width=1000, height=50)
    second = LayoutBox("second", y=200, x=0, width=1000, height=50)

    stops = [
        _stop(ROW, first, LayoutBox("a", y=110, x=0, width=100, height=30)),
        _stop(ROW, second, LayoutBox("b", y=210, x=0, width=100, height=30)),
        _stop(ROW, first, LayoutBox("c", y=120, x=200, width=100, height=30)),
    ]

    regressions = reading_order_regressions(stops)
    assert len(regressions) == 1
    assert regressions[0][0] == 2


def test_a_stop_outside_every_layout_container_is_skipped() -> None:
    """Streamlit's scrollable main region takes a Tab stop and is not a control.

    It is a 980x800 box whose corner sits at y=0, so including it put a jump to the
    top of the document into the sequence on every page.
    """

    widget = LayoutBox("widget", y=400, x=0, width=500, height=50)
    stops = [
        _stop(widget, LayoutBox("a", y=410, x=0, width=100, height=30)),
        FocusStop(path=()),  # the scroll region
        _stop(widget, LayoutBox("b", y=410, x=200, width=100, height=30)),
    ]

    assert reading_order_regressions(stops) == []


def test_layout_box_containment_is_not_symmetric() -> None:
    outer = LayoutBox("outer", y=0, x=0, width=100, height=100)
    inner = LayoutBox("inner", y=10, x=10, width=50, height=50)

    assert outer.contains(inner) is True
    assert inner.contains(outer) is False
