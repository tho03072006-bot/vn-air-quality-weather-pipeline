"""Run every Streamlit page against the active demo/serving warehouse.

Absence of an exception is not evidence a page works. A page that silently renders
an empty state, loses its provenance badges, or drops its accessible table raises
nothing at all, so each page declares the content that must be present and this
script fails when it is missing.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

# What this script cannot check, recorded so nobody assumes it does.
#
# AppTest has no DOM and no layout engine, so two of the four defect classes found
# by opening the app in a real browser are out of reach here:
#
#   * Truncated values. Streamlit clips an overflowing metric with an ellipsis;
#     that is CSS, and the server-side value is intact.
#   * Charts wider than their container. An Altair facet has a fixed pixel width
#     and once measured 1021px inside a 790px column; pixel geometry does not
#     exist in AppTest.
#
# Both need a browser driving the rendered page. The two below -- empty charts and
# leaked markdown directives -- are visible in the element tree, and between them
# they cover the two most damaging defects found so far.


@dataclass(frozen=True, slots=True)
class PageExpectation:
    """What has to be on the page for it to count as rendered."""

    page: str
    title_contains: str
    # Substrings that must appear somewhere in the page's text output.
    required_text: tuple[str, ...] = ()
    # Widget labels that must exist, so a filter cannot quietly disappear.
    required_widget_labels: tuple[str, ...] = ()
    min_dataframes: int = 0
    forbidden_text: tuple[str, ...] = field(default=("nan µg/m³", "nan °C", "None µg/m³"))


PAGES: tuple[PageExpectation, ...] = (
    PageExpectation(
        page="today.py",
        title_contains="hôm nay",
        required_text=(
            "Mô hình CAMS",
            "Điều cần biết",
            "Số liệu tính lúc",
            # The ranked hours must stay labelled as individual hours until a
            # contiguous-window model exists.
            "chưa phải một khoảng liên tục",
            # Asserted on the body text, not the expander title: the caveats matter
            # more than the heading that hides them.
            "không phải VN_AQI",
            "không phải tư vấn y tế",
            # Answer-first hero and the 24h timeline with its threshold annotation.
            "PM2.5 trong 24 giờ tới",
            "Bảng 2 QĐ 1459",
            "ngưỡng nồng độ, không phải giá trị VN_AQI",
        ),
        required_widget_labels=("Địa điểm chính",),
        min_dataframes=1,
    ),
    PageExpectation(
        page="national_map.py",
        title_contains="Bản đồ",
        required_text=(
            # Legend must be present, with its numeric ranges as text.
            "Thang màu",
            "0–25",
            "Không có dữ liệu",
            # The modelled-anchor caveat must survive any redesign.
            "không phải trạm quan trắc",
            "Trạng thái theo tỉnh/thành",
        ),
        required_widget_labels=("Chỉ số hiển thị",),
        min_dataframes=1,
    ),
    PageExpectation(
        page="forecast.py",
        title_contains="báo",
        required_text=(
            # Each pollutant must stay on its own scale. This caption is the promise
            # that goes with resolve_scale(y='independent'); if the panels were
            # collapsed back onto one axis it would be a lie.
            "trục y độc lập",
            "không so sánh độ cao giữa các khung",
            # UV must not share an axis with rain probability again.
            "Chỉ số UV",
            "không cùng thang",
            "Khả năng mưa",
            # The vintage has to be stated, whether or not the two agree.
            "Lần chạy mô hình",
            "Điểm phù hợp ngoài trời",
        ),
        required_widget_labels=("Khoảng dự báo", "Địa điểm chính"),
        min_dataframes=1,
    ),
    PageExpectation(page="custom_location.py", title_contains="địa điểm"),
    PageExpectation(
        page="history.py",
        title_contains="ịch sử",
        # History renders behind a form submit, so only the pre-submit prompt and the
        # source-separation promise are asserted here. Asserting the coverage strip
        # would need the form driven, which belongs with the interaction work.
        required_text=("không trộn thành một chuỗi", "Áp dụng"),
        required_widget_labels=("Khoảng ngày UTC", "Chất ô nhiễm"),
    ),
    PageExpectation(
        page="alerts.py",
        title_contains="ảnh báo",
        # The page must keep saying it cannot send. Nothing in alerts.py delivers a
        # message, and the previous copy implied that configuring two environment
        # variables would make it work.
        required_text=("chỉ mô phỏng, chưa gửi cảnh báo", "không có code gửi tin"),
    ),
    PageExpectation(
        page="pipeline_health.py",
        title_contains="ipeline",
        # PARTIAL is the outcome worth surfacing: nothing errored, so nobody is
        # paged, yet the warehouse is incomplete.
        required_text=("PARTIAL", "Raw tạo mới", "Run mới nhất"),
        min_dataframes=1,
    ),
    PageExpectation(
        page="trust.py",
        title_contains="tin cậy",
        required_text=(
            # The limitations a reader needs before believing a number elsewhere.
            "Một điểm không đại diện cho cả tỉnh",
            "không phải trạm quan trắc",
            "thời điểm hệ thống lấy dữ liệu",
            "không phải VN_AQI",
            # No accuracy figure may be published until a verification fact exists.
            "Chưa có bất kỳ đối chiếu thực nghiệm nào",
            "Quyết định 1459",
        ),
    ),
    PageExpectation(
        page="compare.py",
        title_contains="So sánh",
        required_text=("Bảng xếp hạng", "đơn vị khác nhau"),
        required_widget_labels=("Chọn tối đa 5 tỉnh/thành", "Xếp hạng cho mục đích"),
        min_dataframes=1,
    ),
)


class _VisibleHtmlText(HTMLParser):
    """Collect visible text from ``st.html`` while ignoring CSS and scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and not self._ignored_depth:
            self.parts.append(text)


def _page_text(app: AppTest) -> str:
    """Collect the rendered text of every element type that carries copy."""

    parts: list[str] = []
    for collection in (
        app.title,
        app.header,
        app.subheader,
        app.markdown,
        app.caption,
        app.text,
        app.warning,
        app.error,
        app.info,
        app.success,
    ):
        parts.extend(str(element.value) for element in collection)
    for metric in app.metric:
        parts.extend([str(metric.label), str(metric.value)])

    # AppTest 1.60 exposes st.html as an UnknownElement through get("html"), not
    # through a typed ``app.html`` collection. Parse only visible text so the map
    # legend remains structurally verifiable without letting words in CSS selectors
    # satisfy a required-copy assertion.
    try:
        html_elements = app.get("html")
    except Exception:  # noqa: BLE001 - an absent element type is a valid page state
        html_elements = ()
    for element in html_elements:
        parser = _VisibleHtmlText()
        parser.feed(str(getattr(element.proto, "body", "")))
        parts.extend(parser.parts)
    return "\n".join(parts)


def _widget_labels(app: AppTest) -> set[str]:
    """Collect labels across every selector type the pages actually use.

    segmented_control and pills belong here as much as selectbox does. Leaving them
    out made this script report a missing filter on a page that had one, which is
    the failure mode that erodes trust in a check like this fastest.
    """

    labels: set[str] = set()
    for collection in (
        app.selectbox,
        app.multiselect,
        app.radio,
        app.slider,
        app.select_slider,
        app.date_input,
        app.segmented_control,
        app.pills,
        app.toggle,
        app.checkbox,
    ):
        labels.update(str(widget.label) for widget in collection)
    return labels


# There is deliberately no expander-label check. AppTest 1.60 reports zero elements
# in app.expander for these pages even when the expander renders, so such a check
# would fail on working code. The expander's body text is collected by app.markdown
# regardless of nesting, so the caveats inside it are asserted directly instead --
# which is the part that has to be there anyway.


def _chart_problems(app: AppTest) -> list[str]:
    """Assert every Vega chart on the page actually received rows.

    Scope, stated precisely because it is narrower than it first looks: this catches
    a chart handed no data. It would NOT have caught the datetime64[us] defect, where
    the rows were present and Vega simply could not parse their timestamps -- the
    chart drew nothing while its data was intact. That failure is covered instead by
    test_microsecond_timestamps_are_cast_to_nanoseconds, at the point where the cast
    happens. Verifying the rendered pixels needs a real browser, which AppTest is not.
    """

    problems: list[str] = []
    # AppTest raises rather than returning empty when a page drew no charts at all,
    # which is a legitimate state for Alerts, Trust and pre-submit History. The
    # exception type is an implementation detail of the element tree, so catch
    # broadly: a page with no charts must never fail this script.
    try:
        charts = list(app.get("vega_lite_chart"))
    except Exception:  # noqa: BLE001
        return problems

    for index, element in enumerate(charts):
        # proto.spec, not .value: a chart without an explicit key has no session
        # state entry, and .value raises looking one up.
        try:
            spec = json.loads(str(element.proto.spec))
        except (AttributeError, TypeError, ValueError):
            problems.append(f"chart {index}: spec is not readable JSON")
            continue
        # Streamlit carries the rows in proto.datasets and leaves the spec holding
        # only a reference to them, so counting the spec alone reports every chart
        # as empty. Inline values still appear in the spec for small literal frames
        # such as the threshold rule.
        row_count = 0
        for dataset in getattr(element.proto, "datasets", []) or []:
            data = getattr(dataset, "has_parsed_data", None)
            row_count += 1 if data is None or data else 0
        inline = spec.get("data", {})
        if isinstance(inline, dict) and isinstance(inline.get("values"), list):
            row_count += len(inline["values"])
        for values in (spec.get("datasets") or {}).values():
            if isinstance(values, list):
                row_count += len(values)
        if row_count == 0:
            problems.append(f"chart {index}: rendered with no data rows")
    return problems


# Streamlit's badge/colour directives are markdown. Passing st.badge a hex colour
# does not raise -- it emits the directive unrendered, so the map legend once
# printed ":#16a34a-badge[0-25]" as literal text where a coloured chip belonged.
#
# The hash is required. A named colour (":green-badge[...]") is the correct source
# form and renders fine; only a hex colour leaks through as text. A first version of
# this pattern made the hash optional and flagged every page in the app, which is
# the failure mode of a check that cannot pass on correct code.
_UNRENDERED_DIRECTIVE = re.compile(r":#[0-9a-fA-F]{3,8}-(?:badge|background)\[")


def _directive_problems(text: str) -> list[str]:
    leaked = sorted(set(_UNRENDERED_DIRECTIVE.findall(text)))
    if not leaked:
        return []
    return [f"hex colour directive leaked as literal text: {', '.join(leaked)}"]


def _check(app: AppTest, expectation: PageExpectation) -> list[str]:
    problems = [f"raised {exception.value}" for exception in app.exception]
    text = _page_text(app)
    lowered = text.lower()

    if expectation.title_contains.lower() not in lowered:
        problems.append(f"title missing {expectation.title_contains!r}")
    for needle in expectation.required_text:
        if needle.lower() not in lowered:
            problems.append(f"missing required text {needle!r}")
    for needle in expectation.forbidden_text:
        if needle.lower() in lowered:
            problems.append(f"rendered an unformatted missing value: {needle!r}")

    labels = _widget_labels(app)
    for label in expectation.required_widget_labels:
        if not any(label.lower() in existing.lower() for existing in labels):
            problems.append(f"missing widget labelled {label!r}")

    if len(app.dataframe) < expectation.min_dataframes:
        problems.append(
            f"expected at least {expectation.min_dataframes} data table(s), "
            f"found {len(app.dataframe)}"
        )

    problems.extend(_directive_problems(text))
    problems.extend(_chart_problems(app))
    return problems


def _check_interactions(app: AppTest) -> list[str]:
    """Change a widget and assert the page responded.

    Rendering once proves the happy path only. These drive the controls a reader
    actually touches, because a filter that renders but does not re-query is exactly
    the kind of fault that raises nothing and looks fine in a screenshot.
    """

    problems: list[str] = []

    # Forecast horizon: switching to 24 hours must shrink the series.
    app.switch_page("app_pages/forecast.py").run(timeout=60)
    if not app.segmented_control:
        return ["forecast.py: no segmented control to drive"]
    long_rows = _hour_count(app)
    horizon = next((c for c in app.segmented_control if "Khoảng" in str(c.label)), None)
    if horizon is None:
        problems.append("forecast.py: horizon control missing")
    else:
        horizon.set_value(24).run(timeout=60)
        short_rows = _hour_count(app)
        if short_rows is None or long_rows is None:
            problems.append("forecast.py: could not read the hour count from the page")
        elif short_rows >= long_rows:
            problems.append(
                f"forecast.py: horizon 24 returned {short_rows} hours, not fewer than "
                f"the {long_rows} at 72 — the control is not re-querying"
            )

    # Map metric switch: the legend must follow the selected metric.
    app.switch_page("app_pages/national_map.py").run(timeout=60)
    metric_control = next((c for c in app.segmented_control if "Chỉ số" in str(c.label)), None)
    if metric_control is None:
        problems.append("national_map.py: metric control missing")
    else:
        metric_control.set_value("outdoor_score").run(timeout=60)
        text = _page_text(app)
        # Bands specific to the outdoor score, absent from the PM2.5 ramp.
        if "Nên hạn chế" not in text:
            problems.append("national_map.py: legend did not switch to the outdoor-score bands")
        if "0–25" in text:
            problems.append("national_map.py: PM2.5 legend bands survived the metric switch")

    problems.extend(_check_history_form(app))
    problems.extend(_check_custom_location_form(app))
    return problems


def _check_history_form(app: AppTest) -> list[str]:
    """Submit the history filter form and assert the loaded state, not the prompt.

    Everything below the submit had never been exercised by anything. Driving it
    found two defects: the pollutant defaulted to the alphabetically first option,
    NO2, for which the warehouse holds no observed rows at all, so the default
    submit could only ever return nothing; and the coverage strip grouped by day, so
    a day with no rows produced no group and the page concluded every day was
    complete while ten of twelve held nothing.
    """

    problems: list[str] = []
    app.switch_page("app_pages/history.py").run(timeout=60)
    # By label, not by index. app.button[0] is the header's "Đọc lại warehouse"
    # refresh control, so an index-based click drove the wrong widget and reported
    # the page as broken while it was fine.
    submit = next((b for b in app.button if "Áp dụng" in str(b.label)), None)
    if submit is None:
        return ["history.py: no 'Áp dụng' submit button to drive"]
    before = _page_text(app)
    if "Chọn bộ lọc rồi nhấn" not in before:
        problems.append("history.py: pre-submit prompt missing, so the form state is unclear")

    submit.click().run(timeout=90)
    text = _page_text(app)

    # Discriminating by construction: this is the exact string the page printed
    # before the default was changed, and it is what a first-time reader saw.
    if "Không có dữ liệu phù hợp" in text:
        problems.append(
            "history.py: the default filter returned no rows — a reader's first submit "
            "must not look like a broken pipeline"
        )
    if "Chọn bộ lọc rồi nhấn" in text:
        problems.append("history.py: still showing the pre-submit prompt after clicking Áp dụng")
    if "Độ phủ theo ngày" not in text:
        problems.append("history.py: coverage strip absent from the submitted state")
    if not app.metric:
        problems.append("history.py: no KPIs rendered after submit")

    # The coverage claim must not contradict the coverage data.
    #
    # The strip has three legitimate outcomes, not two: every day complete (caption),
    # some day entirely absent (warning), or days that are present but short of 24
    # hours (the incomplete table, with neither string). The first version of this
    # check demanded one of the first two and so failed against the demo warehouse,
    # where every day sits at 21/24 -- partially incomplete, none empty. The page was
    # right and the check was wrong; the check is what changed.
    #
    # Scope, stated so nobody over-trusts this arm: it checks that the page accounts
    # for its data without contradicting itself. It does NOT catch a wrong
    # build_coverage -- measured, not assumed: against the old grouped-by-day
    # implementation this whole function still passes, because that version renders a
    # self-consistent "every day complete" claim from data it silently dropped. The
    # coverage logic is guarded by tests/test_coverage_view.py, where two tests fail
    # on that implementation. Anything stronger here has to compare the page's claim
    # against the warehouse independently.
    complete_claim = "Mọi ngày trong khoảng đã chọn đều đủ 24 giờ."
    empty_days = "không có số đo nào"
    if complete_claim in text and empty_days in text:
        problems.append("history.py: page claims every day is complete and also reports empty days")
    if complete_claim in text and app.dataframe:
        problems.append(
            "history.py: page claims every day is complete while still tabling incomplete days"
        )
    if complete_claim not in text and empty_days not in text and not app.dataframe:
        problems.append(
            "history.py: coverage strip reports neither completeness, nor empty days, nor a "
            "table of incomplete days — it accounts for none of its data"
        )

    return problems


def _check_custom_location_form(app: AppTest) -> list[str]:
    """Search a province with a fake geocoder and verify registry-first ordering."""

    import dashboard.runtime as runtime
    from vn_air_quality_weather.clients.geocoding import GeocodingResult

    geocoding_calls: list[tuple[str, int]] = []

    class FakeGeocodingClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> FakeGeocodingClient:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def search(self, query: str, *, count: int) -> tuple[GeocodingResult, ...]:
            geocoding_calls.append((query, count))
            # Deliberately relevant by raw name but geographically wrong. The
            # registry representative must still be the first selectable result.
            return (
                GeocodingResult(
                    geonames_id=1,
                    name="An Giang",
                    latitude=14.3397,
                    longitude=109.1000,
                    timezone="Asia/Ho_Chi_Minh",
                    country_code="VN",
                    admin1="Gia Lai",
                    admin2="Huyện Phù Mỹ",
                    feature_code="PPL",
                ),
            )

    problems: list[str] = []
    with patch.object(runtime, "OpenMeteoGeocodingClient", FakeGeocodingClient):
        runtime.cached_location_search.clear()
        try:
            app.switch_page("app_pages/custom_location.py").run(timeout=60)
            query_input = next(
                (
                    widget
                    for widget in app.text_input
                    if str(widget.label) == "Tên địa danh tại Việt Nam"
                ),
                None,
            )
            submit = next((button for button in app.button if str(button.label) == "Tìm"), None)
            if query_input is None:
                return ["custom_location.py: location-name input missing"]
            if submit is None:
                return ["custom_location.py: no 'Tìm' submit button to drive"]

            query_input.set_value("An Giang")
            submit.click().run(timeout=60)
        finally:
            runtime.cached_location_search.clear()

    if app.exception:
        problems.extend(
            f"custom_location.py: raised {exception.value}" for exception in app.exception
        )
        return problems
    if geocoding_calls != [("An Giang", 10)]:
        problems.append(
            "custom_location.py: fake geocoder was not called exactly once for "
            f"'An Giang' with count=10 (calls={geocoding_calls!r})"
        )

    results = next(
        (
            widget
            for widget in app.selectbox
            if str(widget.label).startswith("Kết quả tìm kiếm cho")
        ),
        None,
    )
    if results is None:
        problems.append("custom_location.py: search results selector missing after submit")
        return problems

    expected_first = "An Giang — điểm đại diện tỉnh (Rạch Giá)"
    options = list(results.options)
    if not options or options[0] != expected_first:
        first = options[0] if options else None
        problems.append(
            "custom_location.py: registry representative is not the first search result "
            f"(expected={expected_first!r}, actual={first!r})"
        )
    if len(options) < 2 or "gần điểm đại diện Gia Lai" not in options[1]:
        problems.append(
            "custom_location.py: fake geocoding result was not rendered after the registry result"
        )

    return problems


def _hour_count(app: AppTest) -> int | None:
    """Read the 'N giờ khả dụng' figure the forecast page prints."""

    for element in app.caption:
        value = str(element.value)
        if "giờ khả dụng" in value:
            digits = "".join(
                ch for ch in value.split("giờ khả dụng")[0] if ch.isdigit() or ch == " "
            )
            parts = [part for part in digits.split() if part]
            if parts:
                return int(parts[-1])
    return None


def main() -> None:
    app = AppTest.from_file("dashboard/app.py").run(timeout=60)
    failures: list[str] = []
    for expectation in PAGES:
        app.switch_page(f"app_pages/{expectation.page}").run(timeout=60)
        problems = _check(app, expectation)
        if problems:
            failures.extend(f"{expectation.page}: {problem}" for problem in problems)
            print(f"FAIL dashboard/{expectation.page}")
            for problem in problems:
                print(f"     {problem}")
        else:
            print(f"PASS dashboard/{expectation.page}")

    interaction_problems = _check_interactions(app)
    if interaction_problems:
        failures.extend(f"interaction: {problem}" for problem in interaction_problems)
        print("FAIL widget interactions")
        for problem in interaction_problems:
            print(f"     {problem}")
    else:
        print("PASS widget interactions")

    if failures:
        print(f"\n{len(failures)} problem(s) across {len(PAGES)} pages", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
