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

import json

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src import auth, charts, filters, queries
from src.chart_generator import (
    UnsafeQueryError,
    build_chart_spec,
    safe_run_query,
)
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
        "✨ Generate",
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
    """Two-mode renderer per entity: **By proposal** or **By evaluator**.

    Both modes are picker-driven (no date trend, no drift heatmap).
    The team picks the thing they want to investigate and reads the
    full breakdown with grades + reasoning + key weakness.
    """
    st.markdown(f"### {label}")

    # ── Mode selector
    mode = st.radio(
        "View by",
        options=["By proposal", "By evaluator"],
        horizontal=True,
        key=f"mode_{entity_key}",
        label_visibility="collapsed",
    )

    if mode == "By proposal":
        _by_proposal(entity_key)
    else:
        _by_evaluator(entity_key)


def _by_proposal(entity_key: str) -> None:
    """Pick one entity → see every evaluator's grade + reasoning."""
    listing = queries.entity_list_with_scores(entity_key, days=f.days)
    if listing.empty:
        st.info("No scored entities in the selected window.")
        return

    # Picker with avg-grade + scorer-count chip so the picker itself is informative.
    options = {
        row["id"]: f"{row['title'][:80]}  ·  avg {row['avg_grade']:.2f}  ·  {row['scorer_count']} scorers  ·  {row['owner'] or '—'}"
        for _, row in listing.iterrows()
    }
    picked_id = st.selectbox(
        "Pick a proposal",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        key=f"prop_pick_{entity_key}",
    )
    if not picked_id:
        return

    meta = listing[listing["id"] == picked_id].iloc[0]
    cols = st.columns(4)
    cols[0].metric("Avg grade", f"{meta['avg_grade']:.2f}")
    cols[1].metric("Scorers", int(meta["scorer_count"]))
    cols[2].metric("Last scored", str(meta["last_scored_on"]))
    cols[3].metric("Owner", meta["owner"] or "—")

    detail = queries.entity_score_breakdown(entity_key, picked_id)
    if detail.empty:
        st.info("No score rows found for this proposal.")
        return

    st.markdown(f"#### All scorer grades for `{meta['title'][:80]}`")
    st.caption(f"_{len(detail)} scores · sorted by grade ascending (weakest first)._")

    # Grade-band filter same as before
    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"grade_filter_byprop_{entity_key}",
    )
    if bands:
        detail = detail[detail["grade"].isin(bands)]

    for _, r in detail.iterrows():
        scorer_label = label_for(r["scorer_id"])
        pill = _grade_pill(r["grade"], r["grade_label"])
        with st.expander(f"{scorer_label}", expanded=False):
            st.markdown(pill, unsafe_allow_html=True)
            if r["reasoning"]:
                st.markdown("**Reasoning**")
                st.write(r["reasoning"])
            kw = (r["key_weakness"] or "").strip()
            if kw and kw.lower() not in {"none", "n/a"}:
                st.markdown("**Key weakness**")
                st.write(kw)
            st.caption(
                f"Scorer: `{r['scorer_id']}`  ·  Model: `{r['model'] or 'unknown'}`  ·  Scored on {r['scored_on']}"
            )


def _by_evaluator(entity_key: str) -> None:
    """Pick one evaluator → see every entity it scored, with mean + reasoning."""
    listing = queries.evaluator_list_with_scores(entity_key, days=f.days)
    if listing.empty:
        st.info("No evaluator activity in the selected window.")
        return

    options = {
        row["scorer_id"]: f"{label_for(row['scorer_id'])}  ·  mean {row['mean_grade']:.2f}  ·  {int(row['entities_scored'])} entities  ·  {int(row['total_scores'])} scores"
        for _, row in listing.iterrows()
    }
    picked = st.selectbox(
        "Pick an evaluator",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        key=f"eval_pick_{entity_key}",
    )
    if not picked:
        return

    meta = listing[listing["scorer_id"] == picked].iloc[0]
    cols = st.columns(5)
    cols[0].metric("Mean grade", f"{meta['mean_grade']:.2f}")
    cols[1].metric("Std dev", f"{meta['stddev']:.2f}")
    cols[2].metric("Min / Max", f"{int(meta['min_grade'])} / {int(meta['max_grade'])}")
    cols[3].metric("Entities scored", int(meta["entities_scored"]))
    cols[4].metric("Total scores", int(meta["total_scores"]))

    detail = queries.evaluator_score_breakdown(entity_key, picked, days=f.days)
    if detail.empty:
        st.info("No score rows found for this evaluator.")
        return

    st.markdown(f"#### Every entity scored by `{label_for(picked)}`")
    st.caption(f"_{len(detail)} entities · sorted by grade ascending (weakest first)._")

    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"grade_filter_byeval_{entity_key}",
    )
    if bands:
        detail = detail[detail["grade"].isin(bands)]

    for _, r in detail.iterrows():
        pill = _grade_pill(r["grade"], r["grade_label"])
        with st.expander(
            f"{r['scored_on']}  ·  {r['entity_title'][:80]}  ·  {r['owner'] or '—'}",
            expanded=False,
        ):
            st.markdown(pill, unsafe_allow_html=True)
            if r["reasoning"]:
                st.markdown("**Reasoning**")
                st.write(r["reasoning"])
            kw = (r["key_weakness"] or "").strip()
            if kw and kw.lower() not in {"none", "n/a"}:
                st.markdown("**Key weakness**")
                st.write(kw)
            st.caption(f"Entity id: `{r['entity_id']}`  ·  Model: `{r['model'] or 'unknown'}`")


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

        cross_df = queries.cross_entity_trend(days=f.days, granularity=f.granularity)

        st.markdown("#### Quality trend by surface")
        st.altair_chart(
            charts.cross_entity_chart(cross_df),
            use_container_width=True,
        )

        # ── Tufte-skill output: same data, small-multiples treatment.
        # Generated via the `.claude/skills/wingrants-charts` project
        # skill which wraps the upstream `tufte` skill. Read the
        # audit-trail comment in `charts.small_multiples_trend` for
        # the principle-by-principle accounting.
        st.markdown("#### Tufte small multiples (skill test)")
        st.caption(
            "Same underlying data, redrawn against the ten Tufte "
            "principles by the `wingrants-charts` project skill — "
            "one panel per surface, shared 1-5 scale, no gridlines, "
            "no frame, single accent colour, direct titles."
        )
        st.altair_chart(charts.small_multiples_trend(cross_df), use_container_width=False)

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


# ── Tab 4 — Generate a custom chart with Claude + Tufte skill ───
with tabs[4]:
    st.markdown("### ✨ Generate a chart from your own query")
    st.caption(
        "Paste a SELECT, tell Claude what story to surface, and the "
        "`wingrants-charts` skill (which imports the upstream Tufte "
        "principles) returns a Vega-Lite spec. No code-exec — pure "
        "declarative JSON, rendered through Streamlit's Vega-Lite "
        "embed."
    )

    # ── Example pickers so the team doesn't start from a blank page.
    examples = {
        "Weekly mean grade for research notes": (
            "SELECT date_trunc('week', scored_at)::date AS week,\n"
            "       AVG(grade)::numeric(4,2) AS avg_grade,\n"
            "       COUNT(*) AS scores\n"
            "FROM concept_note_scores\n"
            "WHERE scored_at >= NOW() - INTERVAL '180 days'\n"
            "  AND grade IS NOT NULL\n"
            "GROUP BY 1 ORDER BY 1",
            "Time series of how research-note quality evolved over the last 6 months.",
        ),
        "Grade distribution by scorer (top 10 by volume)": (
            "WITH top_scorers AS (\n"
            "  SELECT evaluator_id FROM proposal_scores\n"
            "  WHERE scored_at >= NOW() - INTERVAL '90 days'\n"
            "  GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 10\n"
            ")\n"
            "SELECT s.evaluator_id AS scorer_id,\n"
            "       s.grade,\n"
            "       COUNT(*) AS n\n"
            "FROM proposal_scores s\n"
            "JOIN top_scorers t ON t.evaluator_id = s.evaluator_id\n"
            "WHERE s.scored_at >= NOW() - INTERVAL '90 days'\n"
            "  AND s.grade IS NOT NULL\n"
            "GROUP BY 1, 2 ORDER BY 1, 2",
            "How does each of the 10 most-active AI-draft evaluators distribute their grades? "
            "Small multiples might be a good fit.",
        ),
        "Strategy-note grade mean vs stdev per week": (
            "SELECT date_trunc('week', scored_at)::date AS week,\n"
            "       AVG(grade)::numeric(4,2) AS mean_grade,\n"
            "       COALESCE(STDDEV(grade), 0)::numeric(4,2) AS stddev,\n"
            "       COUNT(*) AS scores\n"
            "FROM strategy_note_scores\n"
            "WHERE scored_at >= NOW() - INTERVAL '180 days'\n"
            "GROUP BY 1 ORDER BY 1",
            "Did strategy-note variance widen as the prompt evolved? Show mean and stddev together.",
        ),
    }

    preset = st.selectbox(
        "Start from an example (or write your own below):",
        options=["— blank —"] + list(examples.keys()),
        index=1,
    )
    default_sql, default_intent = ("", "")
    if preset != "— blank —":
        default_sql, default_intent = examples[preset]

    sql = st.text_area(
        "SELECT query (read-only, single statement)",
        value=default_sql,
        height=200,
        key="gen_sql",
    )
    intent = st.text_input(
        "What should the chart show? (optional — leave blank to let Claude pick)",
        value=default_intent,
        key="gen_intent",
    )

    if st.button("Generate chart", type="primary", key="gen_btn"):
        try:
            with st.spinner("Running query…"):
                df, capped_sql = safe_run_query(sql)
        except UnsafeQueryError as exc:
            st.error(f"Query rejected: {exc}")
            df = None
        except Exception as exc:
            st.error(f"Query failed: {exc}")
            df = None

        if df is not None and not df.empty:
            # Pre-map scorer ids to human labels so whatever chart Claude
            # designs renders the names by default.
            for col in df.columns:
                if col.lower() in {"scorer_id", "scorer", "evaluator_id"}:
                    df[col] = df[col].astype(str).map(label_for)

            st.success(f"Query returned {len(df):,} rows.")

            col_data, col_chart = st.columns([1, 2])
            with col_data:
                st.markdown("##### Data sample")
                st.dataframe(df.head(15), hide_index=True, use_container_width=True)

            with col_chart:
                try:
                    with st.spinner("Claude is drafting a Tufte-compliant spec…"):
                        spec = build_chart_spec(df, intent)
                except Exception as exc:
                    st.error(f"Couldn't build spec: {exc}")
                else:
                    st.markdown("##### Generated chart")
                    try:
                        st.vega_lite_chart(df, spec, use_container_width=True)
                    except Exception as exc:
                        st.error(f"Vega-Lite render failed: {exc}")
                    with st.expander("View the Vega-Lite spec Claude returned"):
                        st.code(json.dumps(spec, indent=2), language="json")
        elif df is not None:
            st.warning("Query ran but returned zero rows.")


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
