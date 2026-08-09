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
