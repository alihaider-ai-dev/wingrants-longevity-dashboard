"""
Sidebar filters.

Currently we only expose two:
  1. Lookback window (in days)  — same control drives every query, so
     comparison across entity tabs uses the same horizon.
  2. Time granularity (day / week / month) — flows into the trend
     chart's date_trunc.

Kept in its own module so future filters (per-model, per-cluster) can
be added without growing app.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.db import healthcheck


@dataclass
class Filters:
    days: int
    granularity: str  # "day" | "week" | "month"


def sidebar() -> Filters:
    with st.sidebar:
        st.markdown(
            "<h3 style='font-family:Fraunces,Georgia,serif;margin:0 0 8px;color:#1A1530;'>"
            "Filters</h3>",
            unsafe_allow_html=True,
        )

        days = st.select_slider(
            "Lookback window",
            options=[7, 14, 30, 60, 90, 180, 365, 730],
            value=90,
            format_func=lambda n: f"Last {n} days" if n < 365 else f"Last {n // 365} year{'s' if n // 365 > 1 else ''}",
        )

        granularity = st.radio(
            "Bucket size",
            options=["day", "week", "month"],
            index=1,
            horizontal=True,
        )

        st.divider()

        if healthcheck():
            st.success("Connected to database", icon="🟢")
        else:
            st.error("Database unreachable — check `db_url` in secrets.", icon="🔴")

        if st.button("🔄 Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    return Filters(days=days, granularity=granularity)
