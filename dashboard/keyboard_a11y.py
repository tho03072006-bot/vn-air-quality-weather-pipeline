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

from dataclasses import dataclass, field

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


@dataclass(slots=True)
class FocusCycleDetector:
    """Decide when a real Tab walk has completed one whole focus cycle.

    The browser adapter supplies a stable identifier for each focused element and
    whether that element is inside Streamlit's main region. The detector owns the
    judgement: revisiting any element means the sequence has wrapped, while falling
    back to ``body`` ends the walk only after focus has reached ``stMain`` at least
    once. The repeated/body stop is not part of the sequence being judged.

    A generous browser-side press limit may remain as a safety ceiling for a broken
    page, but it must never define the measured sequence. This state machine makes
    the result independent of that ceiling once a real cycle is observed.
    """

    visited_focus_ids: set[str] = field(default_factory=set)
    entered_main: bool = False

    def should_stop(
        self,
        focus_id: str | None,
        *,
        in_main: bool,
        is_body: bool,
    ) -> bool:
        """Return whether the current observation closes the focus walk."""

        if is_body:
            return self.entered_main
        if focus_id is None:
            return False
        if focus_id in self.visited_focus_ids:
            return True
        self.visited_focus_ids.add(focus_id)
        self.entered_main = self.entered_main or in_main
        return False


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

# Streamlit lays a page out in containers: `stElementContainer` wraps one widget,
# `stHorizontalBlock` wraps a row of columns. Those containers are exactly where this
# project's ordering decisions live -- which column a thing goes in, and the order the
# elements were written. What happens *inside* one widget is authored by Streamlit or
# by a third-party component.
LAYOUT_CONTAINER_TESTIDS: frozenset[str] = frozenset({"stElementContainer", "stHorizontalBlock"})


@dataclass(frozen=True, slots=True)
class LayoutBox:
    """One layout container on the path to a focused element."""

    key: str
    y: float
    x: float
    width: float = 0.0
    height: float = 0.0

    def contains(self, other: LayoutBox) -> bool:
        """Whether this box fully encloses another on screen."""

        return (
            self.y <= other.y
            and self.x <= other.x
            and self.y + self.height >= other.y + other.height
            and self.x + self.width >= other.x + other.width
        )


@dataclass(frozen=True, slots=True)
class FocusStop:
    """One Tab stop, reduced to what 2.4.3 is judged on.

    `path` runs outermost container first. It is empty for a stop inside no layout
    container at all. The only one in this app is the main region itself: Streamlit
    gives the scrollable `<section data-testid="stMain">` a tab stop so a keyboard
    user can scroll it, which is a 2.1.1 *feature*. It is a 980x800 region rather
    than a control, and comparing its corner against real controls put a spurious
    jump to y=0 in the sequence on every page.
    """

    path: tuple[LayoutBox, ...]


def _moved_backwards(
    previous: tuple[float, float],
    current: tuple[float, float],
    row_tolerance: float,
) -> bool:
    """Whether the second position reads before the first on screen."""

    previous_y, previous_x = previous
    current_y, current_x = current
    moved_up = current_y < previous_y - row_tolerance
    same_row = abs(current_y - previous_y) <= row_tolerance
    moved_left_in_row = same_row and current_x < previous_x - row_tolerance
    return moved_up or moved_left_in_row


def _first_divergence(
    before: tuple[LayoutBox, ...],
    after: tuple[LayoutBox, ...],
) -> tuple[LayoutBox, LayoutBox] | None:
    """The outermost pair of containers where two stops stop sharing a path.

    None when one path is a prefix of the other, which means one element is nested
    inside the other's container: the same reading position, nothing to compare.
    """

    for outer, inner in zip(before, after, strict=False):
        if outer.key != inner.key:
            return outer, inner
    return None


def reading_order_regressions(
    stops: list[FocusStop],
    *,
    row_tolerance: float = SAME_ROW_TOLERANCE_PX,
) -> list[tuple[int, tuple[float, float]]]:
    """Transitions where Tab jumped backwards in the reading order -- 2.4.3 failures.

    Reading order in a column layout is **hierarchical**, not a flat top-to-bottom
    sweep, and that is what a flat comparison of leaf positions kept getting wrong.
    Going down the left column and then up to the top of the right column moves the
    focused element hundreds of pixels *up* the page while reading perfectly
    sensibly. So two stops are compared at the outermost container where their paths
    diverge: same row, different column -> judged left-to-right; same column,
    different widget -> judged top-to-bottom; same widget -> not compared at all.

    That last case is deliberate. Three false-positive classes were all
    widget-internal, and none is this project's to order:

    * a chart's hover toolbar is absolutely positioned at the top right of its own
      widget, so reaching it before the chart body read as a jump up and right;
    * `st.dataframe` renders its toolbar and its grid canvas in one container, so
      toolbar-then-canvas read as a 793px jump left;
    * the map's attribution control sits at the bottom of the map while its
      navigation button sits at the top, both inside the one map widget.

    Comparing containers by **visual** position rather than document order is what
    keeps this able to fail. A CSS `order` that swaps two columns leaves document
    order untouched while reversing what a reader sees -- the textbook 2.4.3
    violation -- and only a visual comparison catches it.

    The honest limit: a bad focus order *within* one widget is not reported.
    """

    regressions: list[tuple[int, tuple[float, float]]] = []
    previous: FocusStop | None = None
    for index, stop in enumerate(stops):
        if not stop.path:
            continue
        if previous is not None:
            divergence = _first_divergence(previous.path, stop.path)
            if divergence is not None:
                before, after = divergence
                # One container drawn inside the other is layered, not sequenced, so
                # there is no before-and-after between them to get wrong. Two real
                # cases: the map draws its layer-picker on top of itself, and
                # `st.dataframe` wraps its virtualised grid in a box that starts above
                # the document origin. Both are siblings in the DOM that overlap on
                # screen, and comparing their corners produced a jump of hundreds of
                # pixels where a reader sees focus stay inside one component.
                layered = before.contains(after) or after.contains(before)
                if not layered and _moved_backwards(
                    (before.y, before.x), (after.y, after.x), row_tolerance
                ):
                    regressions.append((index, (after.y, after.x)))
        previous = stop
    return regressions


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
        if _moved_backwards(positions[step - 1], positions[step], row_tolerance):
            regressions.append((step, positions[step]))
    return regressions
