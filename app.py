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
from src.heatmap import render_heatmap
from src.quality import grade_with_label, quality_label
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
        "Activity",
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

    Colours come from the canonical palette in src.quality so the chip
    matches the heatmap cell colour 1:1.
    """
    from src.quality import quality_color, quality_label as _ql
    if grade is None:
        return "—"
    g = int(grade)
    bg, fg = quality_color(g)
    label = (grade_label or _ql(g)).upper()
    pill = (
        f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:11px;"
        f"letter-spacing:0.04em;'>"
        f"{g} · {label}</span>"
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
    """Three-mode renderer per feature, with the right WinGrants term
    used everywhere — research notes use 'research note', strategy
    notes use 'strategy note', AI drafts use 'proposal'. We call the
    scoring rubrics 'scorers' across all three for vocabulary
    consistency with the team.
    """
    cfg = queries.ENTITIES[entity_key]
    singular = cfg["singular"]                  # 'research note' / 'strategy note' / 'proposal'
    plural = label.lower()                       # 'research notes' / 'strategy notes' / 'ai drafts'
    scorer_prefix = cfg["scorer_prefix"]

    st.markdown(f"### {label}")
    st.caption(
        f"Every {singular} below is scored by scorers in the "
        f"`{scorer_prefix}-…` family. Pick a {singular} to see all "
        f"scorers that scored it, OR pick a scorer to see every "
        f"{singular} it scored. The heatmap surfaces the whole grid "
        f"so weak patterns are visible at a glance."
    )

    # ── Mode selector — labels use the right singular per feature.
    mode = st.radio(
        "View",
        options=["Heatmap", f"By {singular}", "By scorer"],
        horizontal=True,
        key=f"mode_{entity_key}",
        label_visibility="collapsed",
    )

    if mode == "Heatmap":
        _heatmap_view(entity_key, singular, plural)
    elif mode == "By scorer":
        _by_scorer(entity_key, singular, plural)
    else:
        _by_entity(entity_key, singular, plural)


def _heatmap_view(entity_key: str, singular: str, plural: str) -> None:
    """Scorer × entity grid — red→amber→green by grade.

    The team uses this view to spot patterns in low scores. Weakest
    scorers (lowest mean) float to the top, latest entities sit left.
    Default cell colour ramp matches the BAD/POOR/FAIR/GOOD/EXCELLENT
    palette defined in `src.quality`.
    """
    col_l, col_r = st.columns([3, 2])
    with col_l:
        entity_limit = st.select_slider(
            f"Show last N {plural}",
            options=[10, 20, 30, 50, 80, 120],
            value=30,
            key=f"limit_{entity_key}",
            help="X-axis width. Cap kept low so cells stay readable.",
        )
    with col_r:
        weak_only = st.toggle(
            "Focus on weak scores only (≤ 3)",
            value=False,
            key=f"weak_{entity_key}",
            help="On = hide GOOD + EXCELLENT cells so the failures pop. "
                 "Off = show the whole grid (default).",
        )

    df = queries.heatmap_grid(
        entity_key,
        days=f.days,
        entity_limit=entity_limit,
        weak_only=weak_only,
    )
    if df.empty:
        msg = (
            f"No weak scores (≤ 3) for {plural} in this window — try widening "
            f"the lookback or turning off the weak-only filter."
            if weak_only
            else f"No scored {plural} in the selected window."
        )
        st.info(msg)
        return

    # Shape the long-form data into the columns the renderer expects.
    df = df.copy()
    df["scorer_label"] = df["scorer_id"].map(label_for)
    df["entity_short"] = df["entity_full"].astype(str).str.slice(0, 36)
    # Disambiguate identical short titles (truncation can collide).
    dup_mask = df["entity_short"].duplicated(keep=False)
    if dup_mask.any():
        df.loc[dup_mask, "entity_short"] = (
            df.loc[dup_mask, "entity_short"] + " · " +
            df.loc[dup_mask, "entity_id"].astype(str).str.slice(0, 6)
        )
    df["scored_on"] = df["scored_on"].astype(str)
    df["grade"] = df["grade"].astype(int)

    # Tooltip-friendly truncations — Altair renders one line per tooltip
    # field, so keep these short or the hover box turns into a wall.
    # We use a wider limit (480) for reasoning since that's the main
    # signal the team is hovering for, and a tighter one for weakness.
    def _shorten(s, limit=480):
        if s is None:
            return "—"
        try:
            if pd.isna(s):
                return "—"
        except (TypeError, ValueError):
            pass
        s = str(s).strip()
        if not s or s.lower() in {"none", "n/a", "nan", "null"}:
            return "—"
        s = s.replace("\r\n", " ").replace("\n", " · ")
        return s if len(s) <= limit else s[: limit - 1] + "…"

    if "reasoning" in df.columns:
        df["reasoning_short"] = df["reasoning"].apply(_shorten)
    else:
        df["reasoning_short"] = "—"
    if "key_weakness" in df.columns:
        df["weakness_short"] = df["key_weakness"].apply(
            lambda v: _shorten(v, limit=240)
        )
    else:
        df["weakness_short"] = "—"

    # Headline metric strip — totals so the team sees pattern density.
    cols = st.columns(4)
    cols[0].metric("Scorers in view", int(df["scorer_id"].nunique()))
    cols[1].metric(f"{singular.title()}s in view", int(df["entity_id"].nunique()))
    cols[2].metric("Weak cells (≤ 3)", int((df["grade"] <= 3).sum()))
    cols[3].metric("Mean grade", f"{df['grade'].mean():.2f}")

    # Give every scorer ~22 px of vertical space so labels render
    # cleanly (no more 'every other label dropped' Altair behaviour).
    # We don't cap height — the page just scrolls when there are a lot
    # of scorers, which is the right trade-off given the team needs to
    # see EVERY rubric, not a representative sample.
    n_scorers = int(df["scorer_id"].nunique())
    row_px = 22
    height = max(360, row_px * n_scorers + 80)
    st.altair_chart(
        render_heatmap(df, title_hint=singular, height=height, row_px=row_px),
        use_container_width=True,
    )

    # Top weak rows — flat table mode for when the user wants to
    # actually copy a row out (or scan a long tail).
    st.markdown("##### Weakest cells (1s and 2s)")
    weak = df[df["grade"] <= 2].copy()
    if weak.empty:
        st.caption("_No 1s or 2s in the current view — great._")
    else:
        weak["Quality"] = weak.apply(
            lambda r: grade_with_label(r["grade"], r.get("grade_label")), axis=1
        )
        weak = (
            weak.sort_values(["grade", "scored_on"])
            [["scored_on", "scorer_label", "entity_full", "Quality"]]
            .rename(
                columns={
                    "scored_on": "Scored",
                    "scorer_label": "Scorer",
                    "entity_full": singular.title(),
                }
            )
        )
        st.dataframe(weak, hide_index=True, use_container_width=True)


def _by_entity(entity_key: str, singular: str, plural: str) -> None:
    """Pick one entity (a research note / strategy note / proposal) →
    see every scorer's grade + reasoning for it."""
    listing = queries.entity_list_with_scores(entity_key, days=f.days)
    if listing.empty:
        st.info(f"No scored {plural} in the selected window.")
        return

    options = {
        row["id"]: (
            f"{row['title'][:80]}  ·  avg {row['avg_grade']:.2f}  ·  "
            f"{int(row['scorer_count'])} scorers  ·  {row['owner'] or '—'}"
        )
        for _, row in listing.iterrows()
    }
    picked_id = st.selectbox(
        f"Pick a {singular}",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        key=f"entity_pick_{entity_key}",
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
        st.info(f"No score rows found for this {singular}.")
        return

    st.markdown(f"#### All scorers that scored this {singular}")
    st.caption(
        f"_{len(detail)} scorers · sorted by grade ascending "
        f"(weakest first so the failures read first)._"
    )

    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"grade_filter_byentity_{entity_key}",
    )
    if bands:
        detail = detail[detail["grade"].isin(bands)]

    for _, r in detail.iterrows():
        scorer_label = label_for(r["scorer_id"])
        pill = _grade_pill(r["grade"], r["grade_label"])
        with st.expander(scorer_label, expanded=False):
            st.markdown(pill, unsafe_allow_html=True)
            if r["reasoning"]:
                st.markdown("**Reasoning**")
                st.write(r["reasoning"])
            kw = (r["key_weakness"] or "").strip()
            if kw and kw.lower() not in {"none", "n/a"}:
                st.markdown("**Key weakness**")
                st.write(kw)
            st.caption(
                f"Scorer: `{r['scorer_id']}`  ·  Model: "
                f"`{r['model'] or 'unknown'}`  ·  Scored on {r['scored_on']}"
            )


def _by_scorer(entity_key: str, singular: str, plural: str) -> None:
    """Pick one scorer → see every entity of THIS feature it scored,
    with its mean grade across them all + each individual reasoning."""
    listing = queries.evaluator_list_with_scores(entity_key, days=f.days)
    if listing.empty:
        st.info(f"No scorer activity for {plural} in the selected window.")
        return

    options = {
        row["scorer_id"]: (
            f"{label_for(row['scorer_id'])}  ·  mean {row['mean_grade']:.2f}  ·  "
            f"{int(row['entities_scored'])} {plural}  ·  "
            f"{int(row['total_scores'])} scores"
        )
        for _, row in listing.iterrows()
    }
    picked = st.selectbox(
        "Pick a scorer",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        key=f"scorer_pick_{entity_key}",
    )
    if not picked:
        return

    meta = listing[listing["scorer_id"] == picked].iloc[0]
    cols = st.columns(5)
    cols[0].metric(f"Mean grade across {plural}", f"{meta['mean_grade']:.2f}")
    cols[1].metric("Std dev", f"{meta['stddev']:.2f}")
    cols[2].metric("Min / Max", f"{int(meta['min_grade'])} / {int(meta['max_grade'])}")
    cols[3].metric(f"{singular.title()}s scored", int(meta["entities_scored"]))
    cols[4].metric("Total scores", int(meta["total_scores"]))

    detail = queries.evaluator_score_breakdown(entity_key, picked, days=f.days)
    if detail.empty:
        st.info(f"No score rows found for this scorer on {plural}.")
        return

    st.markdown(
        f"#### Every {singular} that `{label_for(picked)}` scored"
    )
    st.caption(
        f"_{len(detail)} {plural} · sorted by grade ascending "
        f"(weakest first so the failures read first)._"
    )

    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"grade_filter_byscorer_{entity_key}",
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
            st.caption(
                f"{singular.title()} id: `{r['entity_id']}`  ·  Model: "
                f"`{r['model'] or 'unknown'}`"
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
    _entity_tab("ai_draft", "AI drafts")


# ── Tab 4 — Trials ────────────────────────────────────────────────


_FUNNEL_STAGES = [
    "Created",
    "Uploaded",
    "Preflighted",
    "Engine started",
    "Completed",
]

_STAGE_COLOUR = {
    "Created":         ("#F4EEE7", "#6D6682"),  # muted (no signal yet)
    "Uploaded":        ("#FBECC4", "#8F6718"),  # warm sun
    "Preflighted":     ("#F4A988", "#5C2412"),  # coral mid
    "Engine started":  ("#A5D49E", "#1F4B1D"),  # mint
    "Completed":       ("#5BA254", "#FFFFFF"),  # deep mint
}


def _stage_pill(stage: str) -> str:
    bg, fg = _STAGE_COLOUR.get(stage, ("#F4EEE7", "#6D6682"))
    return (
        f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:11px;"
        f"letter-spacing:0.04em;'>{stage.upper()}</span>"
    )


def _fmt_minutes(mins) -> str:
    if mins is None or pd.isna(mins):
        return "—"
    m = float(mins)
    if m < 1:
        return "< 1 min"
    if m < 60:
        return f"{m:.0f} min"
    if m < 24 * 60:
        return f"{m / 60:.1f} h"
    return f"{m / (24 * 60):.1f} d"


_SURFACE_LABELS = {
    "trial":       "Section-1.1 trial",
    "proposal":    "AI draft (paid)",
    "consortium":  "Consortium builder",
    "independent": "Independent eval",
    "research":    "Research note",
    "strategy":    "Strategy note",
}
_SURFACE_COLOUR = {
    "trial":       ("#F4A988", "#5C2412"),
    "proposal":    ("#A5D49E", "#1F4B1D"),
    "consortium":  ("#FBECC4", "#8F6718"),
    "independent": ("#E0D4F0", "#3A1A56"),
    "research":    ("#D0E0F0", "#1A3056"),
    "strategy":    ("#FBE2D0", "#5C3812"),
}


def _surface_pill(surface: str) -> str:
    bg, fg = _SURFACE_COLOUR.get(surface, ("#F4EEE7", "#6D6682"))
    label = _SURFACE_LABELS.get(surface, surface.title())
    return (
        f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:10.5px;"
        f"letter-spacing:0.04em;text-transform:uppercase;'>{label}</span>"
    )


with tabs[4]:
    st.markdown("### Cross-surface activity")
    st.caption(
        "Every user action across the platform, newest-first — trials, paid "
        "AI drafts, consortium builds, independent evaluations, research "
        "notes, strategy notes. The 'Trial STARTED' admin email fires on row "
        "creation, so a user can have that email and still be at 'Created' "
        "if they never moved further."
    )

    col_l, col_r = st.columns([3, 2])
    with col_l:
        surfaces_filter = st.multiselect(
            "Surfaces",
            options=list(_SURFACE_LABELS.keys()),
            default=list(_SURFACE_LABELS.keys()),
            format_func=lambda s: _SURFACE_LABELS.get(s, s),
        )
    with col_r:
        trial_only = st.toggle(
            "Trials only",
            value=False,
            help="On = restrict to is_trial=TRUE rows (Section-1.1 trials).",
        )

    activity = queries.user_activity(days=f.days, trial_only=trial_only)
    if activity.empty:
        st.info("No activity in the selected window.")
    elif surfaces_filter and not all(s in surfaces_filter for s in _SURFACE_LABELS.keys()):
        activity = activity[activity["surface"].isin(surfaces_filter)]

    if not activity.empty:
        # ── Surface × Stage matrix at the top ────────────────────
        cross = (
            activity
            .groupby(["surface", "stage"])
            .size()
            .unstack(fill_value=0)
        )
        # Force a stable column order
        stage_order = ["Created", "Engine started", "Completed", "Failed"]
        for s in stage_order:
            if s not in cross.columns:
                cross[s] = 0
        cross = cross[stage_order]
        cross.index = [_SURFACE_LABELS.get(s, s) for s in cross.index]

        st.markdown("#### Surface × stage matrix")
        st.dataframe(cross, use_container_width=True)

        # ── Per-row cards ─────────────────────────────────────────
        st.markdown(f"#### Activity feed — {len(activity)} events")
        st.caption(
            f"_Newest first. Window: last {f.days} days. Surfaces: "
            f"{', '.join(_SURFACE_LABELS.get(s, s) for s in surfaces_filter)}._"
        )

        # Render the trials funnel-style for trial-surface rows + a
        # compact 1-line card for non-trial rows so the feed stays scannable.
        for _, r in activity.iterrows():
            with st.container(border=True):
                top = st.columns([3, 1, 1, 1])
                with top[0]:
                    title = str(r["item_title"])[:80]
                    user = (
                        (r["user_name"].strip() if isinstance(r["user_name"], str) and r["user_name"].strip()
                         else (r["user_email"] or "—"))
                    )
                    org_str = f" · {r['user_org']}" if r["user_org"] else ""
                    st.markdown(
                        f"**{title}**  \n"
                        f"<span style='color:#6D6682;font-size:11px;'>"
                        f"`{r['item_id']}` · {user} · {r['user_email'] or '—'}{org_str}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with top[1]:
                    st.markdown(_surface_pill(r["surface"]), unsafe_allow_html=True)
                with top[2]:
                    st.markdown(_stage_pill(r["stage"]), unsafe_allow_html=True)
                with top[3]:
                    when = pd.to_datetime(r["when_at"]).strftime("%d %b %H:%M")
                    st.markdown(
                        f"<span style='color:#6D6682;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;'>When</span><br>"
                        f"<span style='font-size:12px;color:#1A1530;'>{when}</span>",
                        unsafe_allow_html=True,
                    )

                # Optional 2nd line — phase + batch + has_error
                bits = []
                if isinstance(r.get("phase"), str) and r["phase"]:
                    bits.append(f"phase=`{r['phase']}`")
                if r.get("batch_job_id"):
                    bits.append(f"batch=`{str(r['batch_job_id'])[:12]}…`")
                if r.get("has_error"):
                    bits.append("⚠️ error column populated")
                if bits:
                    st.caption(" · ".join(bits))

    # ── Trial funnel — kept underneath for the deeper trial view ──
    st.markdown("---")
    st.markdown("### Trial funnel")
    st.caption(
        "Detailed per-trial card view including document uploads + preflight + "
        "elapsed time. Same data as the Trials filter above but with the rich "
        "fields surfaced."
    )

    trials = queries.trial_funnel(days=f.days)
    if trials.empty:
        st.info("No trials kicked off in the selected window.")
    else:
        # ── Funnel strip — counts per stage, in order ──────────────
        counts = trials["stage"].value_counts().to_dict()
        ordered_counts = [counts.get(s, 0) for s in _FUNNEL_STAGES]
        total = int(trials.shape[0])

        cols = st.columns(len(_FUNNEL_STAGES))
        for col, stage, n in zip(cols, _FUNNEL_STAGES, ordered_counts):
            with col:
                pct = (n / total * 100) if total else 0
                col.markdown(_stage_pill(stage), unsafe_allow_html=True)
                col.metric(label="", value=int(n), delta=f"{pct:.0f}% of trials")

        st.markdown("")  # spacer

        # ── Per-trial table ────────────────────────────────────────
        view = trials.copy()
        view["Stage"] = view["stage"].apply(_stage_pill)
        view["User"] = view.apply(
            lambda r: (
                (r["user_name"].strip() if isinstance(r["user_name"], str) and r["user_name"].strip()
                 else (r["user_email"] or "—"))
            ),
            axis=1,
        )
        view["Email"] = view["user_email"].fillna("—")
        view["Org"] = view["user_org"].fillna("—")
        view["Created"] = pd.to_datetime(view["created_at"]).dt.strftime("%d %b %H:%M")
        view["Docs"] = view["doc_count"].astype(int)
        view["Preflight"] = view["preflight_status"].fillna("not_run")
        view["Engine"] = view.apply(
            lambda r: (
                (r["phase"] or "")
                if pd.notna(r["phase"]) and (r["phase"] or "")
                else ("running" if pd.notna(r["batch_job_id"]) else "—")
            ),
            axis=1,
        )
        view["Elapsed"] = view["elapsed_minutes"].apply(_fmt_minutes)
        view["Proposal"] = view["name"].astype(str).str.slice(0, 60)
        view["ID"] = view["proposal_id"]

        # Build the markdown table — st.dataframe doesn't render HTML
        # in cells, so we use markdown with a thin custom style.
        st.markdown("#### Per-trial progress")
        st.caption(
            f"_{total} trial{'s' if total != 1 else ''} in the last {f.days} days — newest first._"
        )

        # Stage filter chips so the team can isolate "stuck at Created"
        # quickly without scrolling.
        filt = st.multiselect(
            "Filter by stage",
            options=_FUNNEL_STAGES,
            default=[],
            help="Empty = show all stages.",
        )
        if filt:
            view = view[view["stage"].isin(filt)]

        # Render as cards — Streamlit's dataframe can't show pills + HTML,
        # and the columns are too wide for one row anyway.
        for _, r in view.iterrows():
            with st.container(border=True):
                top = st.columns([3, 1, 1, 1])
                with top[0]:
                    st.markdown(
                        f"**{r['Proposal']}**  \n"
                        f"<span style='color:#6D6682;font-size:11px;'>"
                        f"`{r['ID']}` · {r['User']} · {r['Email']} · {r['Org']}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                with top[1]:
                    st.markdown(r["Stage"], unsafe_allow_html=True)
                with top[2]:
                    st.markdown(
                        f"<span style='color:#6D6682;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;'>Docs</span><br>"
                        f"<span style='font-size:14px;font-weight:600;color:#1A1530;'>{int(r['Docs'])}</span>",
                        unsafe_allow_html=True,
                    )
                with top[3]:
                    st.markdown(
                        f"<span style='color:#6D6682;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;'>Elapsed</span><br>"
                        f"<span style='font-size:14px;font-weight:600;color:#1A1530;'>{r['Elapsed']}</span>",
                        unsafe_allow_html=True,
                    )

                # Detail row — three sub-columns: timestamps, engine, files
                det = st.columns(3)
                det[0].markdown(
                    f"<span style='color:#6D6682;font-size:11px;'>Created</span><br>"
                    f"<span style='font-size:12px;'>{r['Created']}</span>",
                    unsafe_allow_html=True,
                )
                det[1].markdown(
                    f"<span style='color:#6D6682;font-size:11px;'>Preflight</span><br>"
                    f"<span style='font-size:12px;font-family:monospace;'>{r['Preflight']}</span>",
                    unsafe_allow_html=True,
                )
                det[2].markdown(
                    f"<span style='color:#6D6682;font-size:11px;'>Engine phase</span><br>"
                    f"<span style='font-size:12px;font-family:monospace;'>{r['Engine']}</span>",
                    unsafe_allow_html=True,
                )

                # Uploaded files — only render if there are any
                fns = r.get("doc_filenames")
                if isinstance(fns, str) and fns.strip():
                    st.caption(f"📎 Uploaded: {fns}")


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
