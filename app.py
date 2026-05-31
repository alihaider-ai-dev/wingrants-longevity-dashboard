"""
WinGrants longevity dashboard — Streamlit entry point.

Layout:
  - Sidebar : lookback window + bucket size + DB status
  - 4 tabs  : Overview · Research Notes · Strategy Notes · AI Drafts

Each entity tab renders:
  1. Top-line metric strip
  2. Trend over time (avg grade + percentile ribbon)
  3. Per-scorer drift (heatmap)
  4. Customer cohort (top owners by avg grade)
  5. Drill-down summary table (one row per entity)
  6. Latest score details — every individual score with the LLM's
     reasoning + key weakness, scorer code mapped to a human name.

The Overview tab cross-cuts all surfaces with one shared chart + a
metric strip.

Standalone Scorecards and Consortiums tabs were dropped per user
direction — neither was useful for the longevity study they're
actually running.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src import auth, charts, filters, queries
from src.scorer_names import label_for


# ── Page config (must run before any other Streamlit call) ───────
st.set_page_config(
    page_title="WinGrants longevity",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Auth gate (blocks the rest of the page) ──────────────────────
auth.gate()


# ── Title strip ───────────────────────────────────────────────────
st.markdown(
    """
    <div style="padding:18px 0 8px;">
      <h1 style="font-family:Fraunces,Georgia,serif;font-size:32px;
                 color:#1A1530;margin:0;letter-spacing:-0.005em;">
        WinGrants <em style="color:#D9542E;font-style:italic;">longevity</em>
      </h1>
      <p style="margin:6px 0 0;color:#6D6682;font-size:13px;">
        How quality scores evolve across the WinGrants surfaces we
        actively score — research notes, strategy notes, AI drafts.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ── Filters (sidebar) ─────────────────────────────────────────────
f = filters.sidebar()


# ── Tabs ──────────────────────────────────────────────────────────
tabs = st.tabs(
    [
        "Overview",
        "Research Notes",
        "Strategy Notes",
        "AI Drafts",
    ]
)


# ── Shared renderers ──────────────────────────────────────────────


def _metric_strip(df: pd.DataFrame, label: str) -> None:
    """Top-of-tab metric tiles for a single entity."""
    if df.empty:
        st.info(f"No {label.lower()} scored in the selected window.")
        return
    cols = st.columns(4)
    cols[0].metric("Scored entities", int(df.iloc[0]["entities_scored"] or 0))
    cols[1].metric("Total scores", int(df.iloc[0]["scores"] or 0))
    avg = df.iloc[0]["avg_grade"]
    cols[2].metric("Average grade", f"{float(avg):.2f}" if avg is not None else "—")
    cost = df.iloc[0]["approx_cost_usd"]
    cols[3].metric("Approx cost (USD)", f"${float(cost):.2f}" if cost is not None else "—")


def _grade_pill(grade: int | float | None, grade_label: str | None) -> str:
    """Render `4 · GOOD` style pill markup for the details table.

    Colours are warm-paper brand tokens (mint = ≥4, sun = 3, coral = ≤2).
    """
    if grade is None:
        return "—"
    g = int(grade)
    if g >= 4:
        bg, fg = "#DDF0D9", "#3C7A3A"
    elif g == 3:
        bg, fg = "#FBECC4", "#8F6718"
    else:
        bg, fg = "#FFE2D5", "#D9542E"
    label = (grade_label or "").upper()
    pill = (
        f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:11px;'>"
        f"Grade {g}{' · ' + label if label else ''}</span>"
    )
    return pill


def _score_details(entity_key: str, days: int) -> None:
    """Render an expandable, human-readable per-score view.

    Streamlit's `st.dataframe` doesn't render HTML, so we drop down
    to `st.markdown` and emit one card per score. Capped at 200 rows
    so the page stays responsive even for proposals with 365 scorers.
    """
    df = queries.latest_score_details(entity_key, days=days, limit=200)
    if df.empty:
        st.info("No score details in the selected window.")
        return

    st.markdown(f"_Showing {len(df)} most-recent scores (capped at 200)._")

    # Filter chips for grade band so the team can isolate the weak
    # scores when reading reasoning text.
    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"grade_filter_{entity_key}",
    )
    if bands:
        df = df[df["grade"].isin(bands)]

    for _, r in df.iterrows():
        scorer_label = label_for(r["scorer_id"])
        pill = _grade_pill(r["grade"], r["grade_label"])
        with st.expander(
            f"{r['scored_on']}  ·  {r['entity_title'][:60]}  ·  {scorer_label}",
            expanded=False,
        ):
            st.markdown(pill, unsafe_allow_html=True)
            if r["reasoning"]:
                st.markdown("**Reasoning**")
                st.write(r["reasoning"])
            if r["key_weakness"] and str(r["key_weakness"]).lower() not in {"none", "n/a", ""}:
                st.markdown("**Key weakness**")
                st.write(r["key_weakness"])
            st.caption(f"Scorer: `{r['scorer_id']}`  ·  Model: `{r['model'] or 'unknown'}`")


def _entity_tab(entity_key: str, label: str) -> None:
    """Render the standard view for an entity."""
    st.markdown(f"### {label}")

    # ── Top-line metrics for this entity
    metrics = queries.overview_metrics(days=f.days)
    metric_row = metrics[metrics["entity"] == label]
    _metric_strip(metric_row, label)

    # ── Section 1: Trend
    st.markdown("#### Trend over time")
    st.altair_chart(
        charts.trend_chart(
            queries.trend_over_time(entity_key, days=f.days, granularity=f.granularity)
        ),
        use_container_width=True,
    )

    # ── Section 2: Drift (scorer codes mapped to human names)
    st.markdown("#### Per-scorer drift")
    drift_df = queries.scorer_drift(entity_key, days=f.days)
    if not drift_df.empty:
        drift_df = drift_df.copy()
        drift_df["scorer"] = drift_df["scorer"].map(label_for)
    st.altair_chart(charts.drift_heatmap(drift_df), use_container_width=True)

    # ── Section 3: Cohort
    st.markdown("#### Customer cohort comparison")
    st.altair_chart(
        charts.cohort_distribution(queries.customer_cohort(entity_key, days=max(f.days, 365))),
        use_container_width=True,
    )

    # ── Section 4: per-entity summary table (one row per note/draft)
    st.markdown("#### Drill-down — entities")
    detail = queries.entity_summary(entity_key, days=f.days)
    st.dataframe(detail, hide_index=True, use_container_width=True)
    if not detail.empty:
        st.download_button(
            "Download CSV",
            data=detail.to_csv(index=False).encode("utf-8"),
            file_name=f"wingrants_{entity_key}_summary.csv",
            mime="text/csv",
            key=f"dl_{entity_key}",
        )

    # ── Section 5: per-score details with reasoning + key_weakness
    st.markdown("#### Latest scoring details")
    _score_details(entity_key, days=f.days)


# ── Tab 0 — Overview ──────────────────────────────────────────────
with tabs[0]:
    st.markdown("### Overview")
    metrics = queries.overview_metrics(days=f.days)
    if metrics.empty:
        st.info("No scoring activity in the selected window.")
    else:
        cols = st.columns(4)
        cols[0].metric("Total entities scored", int(metrics["entities_scored"].sum()))
        cols[1].metric("Total scores", int(metrics["scores"].sum()))
        cols[2].metric(
            "Mean grade (all surfaces)",
            f"{metrics['avg_grade'].mean():.2f}" if not metrics["avg_grade"].dropna().empty else "—",
        )
        cols[3].metric(
            "Approx cost (USD)",
            f"${metrics['approx_cost_usd'].sum():.2f}",
        )

        st.markdown("#### Quality trend by surface")
        st.altair_chart(
            charts.cross_entity_chart(queries.cross_entity_trend(days=f.days, granularity=f.granularity)),
            use_container_width=True,
        )

        st.markdown("#### Per-surface summary")
        st.dataframe(metrics, hide_index=True, use_container_width=True)


# ── Tab 1 — Research Notes ────────────────────────────────────────
with tabs[1]:
    _entity_tab("research_note", "Research notes")


# ── Tab 2 — Strategy Notes ────────────────────────────────────────
with tabs[2]:
    _entity_tab("strategy_note", "Strategy notes")


# ── Tab 3 — AI Drafts ─────────────────────────────────────────────
with tabs[3]:
    _entity_tab("ai_draft", "AI drafts (proposals)")


# ── Footer ────────────────────────────────────────────────────────
st.markdown(
    """
    <hr style="border:none;border-top:1px solid #E2D8CA;margin:28px 0 12px;">
    <p style="color:#B3ADC1;font-size:11px;text-align:center;">
      WinGrants longevity dashboard · Streamlit + Postgres ·
      data refreshed every 30 minutes (cache TTL)
    </p>
    """,
    unsafe_allow_html=True,
)
