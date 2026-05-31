"""
WinGrants longevity dashboard — Streamlit entry point.

Layout:
  - Sidebar  : lookback window + bucket size + DB status
  - 5 tabs   : Overview · Research Notes · Strategy Notes · AI Drafts · Consortiums

Every entity tab renders three sections (Trend / Drift / Cohort), then a
drill-down dataframe at the bottom with a one-click CSV export. The
Overview tab cross-cuts all surfaces with one shared chart + a metric
strip.

Why one file for the renderer
-----------------------------
The per-tab render functions are thin compositions of `src/queries.py`
and `src/charts.py` primitives — splitting them into separate files
would just shuffle them around without adding readability, and keeps
the imports + page-config + auth gate all in one place at the top.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ── Load .env BEFORE importing modules that read os.environ ──────
load_dotenv()

from src import auth, charts, filters, queries
from src.consortium_extract import consortium_scores, consortium_trend


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
        How quality scores evolve across every WinGrants surface — research
        notes, strategy notes, AI drafts, standalone scorecards, and
        consortium audits.
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
        "Standalone Scorecards",
        "Consortiums",
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


def _entity_tab(entity_key: str, label: str) -> None:
    """Render the standard three-section view for an entity."""
    st.markdown(f"### {label}")

    # ── Top-line metrics for this entity (filter by entity row).
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

    # ── Section 2: Drift
    st.markdown("#### Per-evaluator drift")
    st.altair_chart(
        charts.drift_heatmap(queries.scorer_drift(entity_key, days=f.days)),
        use_container_width=True,
    )

    # ── Section 3: Cohort
    st.markdown("#### Customer cohort comparison")
    st.altair_chart(
        charts.cohort_distribution(queries.customer_cohort(entity_key, days=max(f.days, 365))),
        use_container_width=True,
    )

    # ── Drill-down table + CSV export
    st.markdown("#### Drill-down")
    detail = queries.entity_summary(entity_key, days=f.days)
    st.dataframe(detail, hide_index=True, use_container_width=True)
    if not detail.empty:
        st.download_button(
            "Download CSV",
            data=detail.to_csv(index=False).encode("utf-8"),
            file_name=f"wingrants_{entity_key}_summary.csv",
            mime="text/csv",
        )


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


# ── Tab 4 — Standalone Scorecards ─────────────────────────────────
with tabs[4]:
    _entity_tab("scorecard", "Standalone scorecards")


# ── Tab 5 — Consortiums (S3 JSONB-sourced) ────────────────────────
with tabs[5]:
    st.markdown("### Consortium audits")
    scores = consortium_scores(days=max(f.days, 365))
    if scores.empty:
        st.info(
            "No consortium audit data in the selected window. If this is "
            "unexpected, check the AWS credentials in `secrets.toml` and "
            "confirm the bucket name matches the BE writer's target."
        )
    else:
        cols = st.columns(4)
        cols[0].metric("Audited consortia", int(scores.shape[0]))
        cols[1].metric(
            "Mean overall score",
            f"{scores['overall_score'].dropna().mean():.2f}" if not scores["overall_score"].dropna().empty else "—",
        )
        cols[2].metric(
            "Issues per audit",
            f"{scores['issues_found'].mean():.1f}" if not scores["issues_found"].dropna().empty else "—",
        )
        cols[3].metric("Distinct owners", scores["owner"].nunique())

        st.markdown("#### Overall score trend")
        st.altair_chart(
            charts.trend_chart(consortium_trend(days=f.days, granularity=f.granularity)),
            use_container_width=True,
        )

        st.markdown("#### Per-pillar audit scores")
        per_pillar = scores.melt(
            id_vars=["id", "title", "owner", "completed_on"],
            value_vars=["completeness", "balance", "eligibility"],
            var_name="pillar",
            value_name="score",
        )
        st.dataframe(per_pillar, hide_index=True, use_container_width=True)

        st.markdown("#### Drill-down")
        st.dataframe(scores, hide_index=True, use_container_width=True)
        st.download_button(
            "Download CSV",
            data=scores.to_csv(index=False).encode("utf-8"),
            file_name="wingrants_consortium_audits.csv",
            mime="text/csv",
        )


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
