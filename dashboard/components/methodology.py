"""Methodology and limitations, in one collapsible place."""

from __future__ import annotations

import streamlit as st

# Previously this text was an st.info banner repeated at the foot of several pages.
# A caveat shown identically on every visit stops being read, which is the opposite
# of what a caveat is for. Collapsed-but-present keeps it available to a reader who
# is deciding whether to trust a number, without spending the same words on a reader
# who already knows.
_LIMITATIONS = [
    (
        "Điểm đại diện, không phải trạm quan trắc",
        "Mỗi tỉnh/thành được đại diện bởi một điểm lưới mô hình CAMS. Đây không phải "
        "trạm đo và không đại diện cho toàn bộ diện tích tỉnh.",
    ),
    (
        "Điểm phù hợp ngoài trời không phải VN_AQI",
        "outdoor_score là một heuristic lập kế hoạch minh bạch, tính từ PM2.5, mưa, "
        "cảm giác nhiệt và UV. Nó không phải chỉ số VN_AQI theo Quyết định "
        "1459/QĐ-TCMT và không phải chỉ số y tế.",
    ),
    (
        "Độ tin cậy chưa phải độ chính xác",
        "Mức tin cậy vẫn suy ra từ lead time, tính đầy đủ của dữ liệu và việc hai "
        "nguồn air/weather có cùng một lần chạy mô hình hay không. Đối chiếu mô "
        "hình–trạm đã có và được công bố, nhưng khoảng cách đó không phải sai số dự "
        "báo: nó trộn sai số mô hình với sai lệch đại diện giữa điểm lưới tỉnh và vị "
        "trí trạm, và hai phần này chưa được tách. Vì vậy sản phẩm không công bố con "
        "số độ chính xác dự báo nào.",
    ),
    (
        "Khung giờ liên tục dùng điểm thấp nhất",
        "Mỗi khoảng gồm 2 hoặc 3 giờ kề nhau đủ dữ liệu. Điểm của khoảng là điểm "
        "giờ kém nhất, và một giờ thiếu dữ liệu sẽ ngắt khoảng thay vì được xem là "
        "điều kiện tốt.",
    ),
    (
        "Không phải tư vấn y tế",
        "Thông tin ở đây dùng để tham khảo và lập kế hoạch. Không dùng để chẩn đoán "
        "hoặc thay thế hướng dẫn của cơ quan y tế.",
    ),
]


def methodology_expander(*, extra: list[tuple[str, str]] | None = None) -> None:
    """Render the shared methodology notes, plus any page-specific additions."""

    with st.expander("Phương pháp và giới hạn", icon=":material/help:"):
        for heading, body in [*_LIMITATIONS, *(extra or [])]:
            st.markdown(f"**{heading}** — {body}")
