"""Pure helpers for the WCAG 2.1 AA keyboard criteria.

Covers the three criteria that are measurable from the DOM without a human at the
keyboard:

* **2.1.1 Keyboard** -- everything operable by mouse must be reachable by Tab.
* **2.4.7 Focus Visible** -- the focused element must look different from the
  unfocused one.
* **2.4.3 Focus Order** -- Tab must walk the page in document order.

Kept free of Playwright so the rules can be tested offline. The browser adapter in
``scripts/verify_keyboard.py`` supplies the observations; every judgement lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

# Elements that are operable by pointer and therefore must be operable by keyboard.
# `a` is deliberately absent: an anchor without href is not interactive, so it is
# decided by `href` in `is_interactive` rather than by tag alone.
INTERACTIVE_TAGS: frozenset[str] = frozenset(
    {"button", "select", "textarea", "input", "summary", "details"}
)

INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
    }
)

# Computed properties compared before and after focus. Any difference counts as an
# indicator under 2.4.7, which asks only that focus be *visible* -- the stricter
# contrast requirement is 2.4.11, a WCAG 2.2 criterion this gate does not claim.
FOCUS_INDICATOR_PROPERTIES: tuple[str, ...] = (
    "outlineStyle",
    "outlineWidth",
    "outlineColor",
    "outlineOffset",
    "boxShadow",
    "borderColor",
    "borderWidth",
    "backgroundColor",
    "color",
    "textDecorationLine",
)


@dataclass(frozen=True, slots=True)
class ElementSnapshot:
    """One candidate element as observed in the page."""

    tag: str
    role: str | None
    href: str | None
    tabindex: str | None
    disabled: bool
    aria_hidden: bool
    label: str
    # Whether the enclosing Streamlit widget holds some other element that Tab can
    # reach. See `unreachable_interactive_elements` for why 2.1.1 turns on this.
    function_reachable_in_widget: bool = False


def _parsed_tabindex(tabindex: str | None) -> int | None:
    """Return the numeric tabindex, or None when absent or unparseable.

    An unparseable value is treated as absent because that is what browsers do:
    `tabindex="banana"` leaves the element at its natural position rather than
    removing it from the sequence.
    """

    if tabindex is None:
        return None
    try:
        return int(tabindex.strip())
    except (ValueError, AttributeError):
        return None


def is_interactive(element: ElementSnapshot) -> bool:
    """Whether the element is operable, and so owes the user keyboard access.

    Disabled and `aria-hidden` elements are excluded: a disabled control is not
    operable by any input method, and an aria-hidden one is not exposed at all, so
    neither owes a Tab stop. Excluding them is what keeps the gate from reporting
    Streamlit's many decorative wrappers as violations.
    """

    if element.disabled or element.aria_hidden:
        return False
    role = (element.role or "").strip().lower()
    if role in INTERACTIVE_ROLES:
        return True
    tag = element.tag.strip().lower()
    if tag == "a":
        return bool(element.href)
    if tag in INTERACTIVE_TAGS:
        return True
    # An author-supplied tabindex is a claim that the element takes focus.
    return _parsed_tabindex(element.tabindex) is not None


def is_keyboard_reachable(element: ElementSnapshot) -> bool:
    """Whether Tab can land on the element.

    `tabindex="-1"` is focusable by script but skipped by Tab, which is exactly the
    state that makes a control mouse-only.
    """

    index = _parsed_tabindex(element.tabindex)
    return index is None or index >= 0


def unreachable_interactive_elements(
    elements: list[ElementSnapshot],
) -> list[ElementSnapshot]:
    """Interactive elements whose *function* Tab cannot reach -- WCAG 2.1.1 failures.

    `function_reachable_in_widget` is what keeps this honest. 2.1.1 requires the
    functionality to be operable from a keyboard, not that every node carry a Tab
    stop. Streamlit's selectbox renders a dropdown-arrow `<button tabindex="-1">`
    beside a combobox `<input>` that *is* reachable and opens the same menu; the
    button duplicates a mouse affordance rather than withholding a function. Reporting
    it produced a finding on almost every page of the first run, all of them false,
    which would have taught a reader to ignore the gate.
    """

    return [
        element
        for element in elements
        if is_interactive(element)
        and not is_keyboard_reachable(element)
        and not element.function_reachable_in_widget
    ]


def focus_indicator_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    """Whether focusing changed any property of this one element that a reader sees.

    Compared over a fixed property list rather than the whole computed style, because
    the whole style contains values that drift for unrelated reasons (layout metrics
    settle, transitions land) and would make every element look like it had an
    indicator.
    """

    return any(before.get(name) != after.get(name) for name in FOCUS_INDICATOR_PROPERTIES)


def focus_indicator_visible(
    layers_before: list[dict[str, str]],
    layers_after: list[dict[str, str]],
) -> bool:
    """Whether focus is visible anywhere on the element or its nearest ancestors.

    The element alone is not enough to judge this. Streamlit paints the focus ring on
    the wrapper `<div>` around an `<input>`, not on the input, so measuring only the
    focused node reported every text field and every selectbox on the app as having no
    indicator -- false on all of them. The ring is real; it is simply painted one or
    two levels up, and a reader sees the widget, not the node that holds DOM focus.

    Layers run inner-to-outer and are compared pairwise, so a change at any level
    counts. Mirrors how the contrast gate composites ancestor backgrounds rather than
    reading the text node in isolation.
    """

    return any(
        focus_indicator_changed(before, after)
        for before, after in zip(layers_before, layers_after, strict=False)
    )


def positive_tabindex_elements(
    elements: list[ElementSnapshot],
) -> list[ElementSnapshot]:
    """Elements with `tabindex` above zero -- WCAG 2.4.3 hazards.

    A positive tabindex pulls an element to the front of the whole-page sequence,
    ahead of everything with a natural position. One of them is enough to make the
    reading order and the tab order disagree for every other control on the page.
    """

    return [
        element
        for element in elements
        if (index := _parsed_tabindex(element.tabindex)) is not None and index > 0
    ]


# Focus may move within this many pixels vertically and still count as the same row,
# which is what lets a left-to-right row of buttons pass.
SAME_ROW_TOLERANCE_PX = 8.0


def focus_order_regressions(
    positions: list[tuple[float, float]],
    *,
    row_tolerance: float = SAME_ROW_TOLERANCE_PX,
) -> list[tuple[int, tuple[float, float]]]:
    """Transitions where Tab jumped backwards on screen -- 2.4.3 failures.

    `positions` is the observed Tab sequence as document-absolute `(y, x)` pixels.
    A regression is a step that moves **up** a row, or leftwards within the same row.

    Measured against **visual** position rather than DOM index, and this distinction
    is the whole point. 2.4.3 requires the focus sequence to preserve meaning and
    operability, and for a sighted keyboard user the meaningful sequence is the one
    they can see. Streamlit renders popovers and tooltips through portals appended at
    the end of the DOM while painting them at the top of the page, so a DOM-index
    comparison reported a backwards jump on six page/viewport combinations where a
    reader sees focus move perfectly sensibly. Those were false.

    Compared against the previous step rather than a running maximum: after one
    genuine jump, every later well-ordered step still sits below that maximum and
    would be reported too. One backwards transition is one defect.
    """

    regressions: list[tuple[int, tuple[float, float]]] = []
    for step in range(1, len(positions)):
        previous_y, previous_x = positions[step - 1]
        current_y, current_x = positions[step]
        moved_up = current_y < previous_y - row_tolerance
        same_row = abs(current_y - previous_y) <= row_tolerance
        moved_left_in_row = same_row and current_x < previous_x - row_tolerance
        if moved_up or moved_left_in_row:
            regressions.append((step, positions[step]))
    return regressions
