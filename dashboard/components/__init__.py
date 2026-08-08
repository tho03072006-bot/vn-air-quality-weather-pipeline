"""Shared Streamlit components.

Rendering lives here rather than in `dashboard/runtime.py` so that module can go
back to being what its name promises: cached data access. Pure formatting sits one
layer further out in `dashboard/view_models.py`, which imports no Streamlit and is
therefore unit tested directly.
"""

from dashboard.components.methodology import methodology_expander
from dashboard.components.metric_cards import metric_row
from dashboard.components.provenance import freshness_badge, source_badges

__all__ = [
    "freshness_badge",
    "methodology_expander",
    "metric_row",
    "source_badges",
]
