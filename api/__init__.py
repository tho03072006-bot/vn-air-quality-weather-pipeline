"""Read-only HTTP access to the marts the dashboard already serves.

A top-level package beside `dashboard/`, not inside `src/`, and deliberately so.
Both are applications that run from the project root against the installed
`vn_air_quality_weather` library; neither is part of the distributable package. The
API reuses `dashboard.data_access`, which holds the serving SQL and imports no
Streamlit, because a second copy of those queries is exactly the failure this
project has now recorded twice.
"""
