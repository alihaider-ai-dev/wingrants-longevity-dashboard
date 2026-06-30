"""
Proposal deep-dive tab — proposal → division → scorers.

Lets the team pick one AI-draft proposal and walk its full scoring
structure:

  1. Proposal picker (newest-first, badged with redraft availability).
  2. Header metric strip (final avg, divisions, scorers, max redrafts).
  3. Division picker — the 8 EC divisions grouped by parent section.
  4. For the picked division:
       a. Redraft trajectory — avg score per redraft pass + the failing
          scorers (id · grade · key weakness) at each pass, read out of
          `proposals.section_versions`.
       b. Final per-scorer breakdown — every scorer's grade + full
          reasoning + key weakness, weakest-first, from `proposal_scores`.

Why two sub-views: `proposal_scores` only retains the FINAL state per
(division, scorer), so full per-scorer reasoning exists once per scorer.
The per-redraft story (how a division climbed pass-to-pass, which
scorers were failing when) lives in `section_versions`, which only
captures the failing subset at each pass — hence trajectory shows
weaknesses, the final breakdown shows everything.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from src import queries
from src.charts import ACCENT_DEEP, INK, INK_SOFT, RULE
from src.proposal_sections import (
    SECTION_ORDER,
    division_label,
    division_section,
    division_sort_key,
)
from src.quality import quality_color
from src.scorer_names import label_for


def _grade_pill(grade, grade_label=None) -> str:
    """`4 · GOOD` chip, colour-matched to the canonical quality palette."""
    if grade is None:
        return "—"
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return "—"
    bg, fg = quality_color(g)
    from src.quality import quality_label as _ql
    label = (grade_label or _ql(g) or "").upper()
    return (
        f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:11px;"
        f"letter-spacing:0.04em;'>{g} · {label}</span>"
    )


def _clean(text, limit: int | None = None) -> str:
    """Tidy a possibly-null reasoning/weakness string for display."""
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(text).strip()
    if not s or s.lower() in {"none", "n/a", "nan", "null"}:
        return ""
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


# ── Redraft trajectory (from section_versions) ──────────────────────


def _trajectory(section_id: str, sv_entry: dict) -> None:
    """Render the per-redraft trajectory for one division."""
    history = sv_entry.get("score_history") or []
    changes = sv_entry.get("change_history") or []
    best = sv_entry.get("best_score")
    rev_count = sv_entry.get("revision_count")

    if not history and not changes:
        st.caption("_No redraft trajectory stored for this division — showing final state only._")
        return

    head = st.columns(3)
    head[0].metric("Best score", f"{float(best):.2f}" if best is not None else "—")
    head[1].metric("Redraft passes", int(rev_count) if rev_count is not None else len(history))
    if history:
        delta = float(history[-1]) - float(history[0]) if len(history) > 1 else 0.0
        head[2].metric("Final pass score", f"{float(history[-1]):.2f}", delta=f"{delta:+.2f}")

    # Score-per-pass line — honest [0,5] domain so the climb reads true.
    if history:
        traj = pd.DataFrame({
            "pass": list(range(1, len(history) + 1)),
            "score": [float(v) for v in history],
        })
        line = (
            alt.Chart(traj)
            .mark_line(
                color=ACCENT_DEEP,
                strokeWidth=2,
                point=alt.OverlayMarkDef(filled=True, size=46, color=ACCENT_DEEP),
            )
            .encode(
                x=alt.X("pass:O", title="Redraft pass", axis=alt.Axis(labelColor=INK_SOFT)),
                y=alt.Y(
                    "score:Q",
                    scale=alt.Scale(domain=[0, 5]),
                    title="Avg score",
                    axis=alt.Axis(values=[0, 1, 2, 3, 4, 5], grid=True,
                                  gridColor=RULE, gridOpacity=0.6, labelColor=INK_SOFT),
                ),
                tooltip=[
                    alt.Tooltip("pass:O", title="Pass"),
                    alt.Tooltip("score:Q", title="Avg score", format=".2f"),
                ],
            )
            .properties(
                height=200,
                title=alt.TitleParams("Average score across redraft passes",
                                      color=INK, fontSize=12, anchor="start"),
            )
            .configure_view(stroke=None)
        )
        st.altair_chart(line, use_container_width=True)

    # Per-pass detail — improvements made + scorers that were failing.
    st.markdown("**Per-pass detail**")
    for entry in changes:
        rev = entry.get("revision")
        rev_label = f"Pass {rev}" if isinstance(rev, int) else str(rev or "earlier").title()
        score = entry.get("score")
        score_str = f" · score {float(score):.2f}" if score is not None else ""
        if entry.get("_summary_of_earlier_attempts"):
            rev_label = "Earlier passes (summarised)"
        with st.expander(f"{rev_label}{score_str}", expanded=False):
            improvements = [i for i in (entry.get("improvements_made") or []) if str(i).strip()]
            if improvements:
                st.markdown("**Improvements made**")
                for imp in improvements:
                    st.markdown(f"- {imp}")
            failing = entry.get("failing_at_entry") or []
            if failing:
                st.markdown("**Failing scorers at this pass**")
                for f in failing:
                    pill = _grade_pill(f.get("grade"), f.get("grade_label"))
                    kw = _clean(f.get("key_weakness"))
                    st.markdown(
                        f"{pill} &nbsp; **{label_for(f.get('id'))}**"
                        + (f"<br><span style='color:#6D6682;font-size:12px;'>{kw}</span>" if kw else ""),
                        unsafe_allow_html=True,
                    )
            remaining = [r for r in (entry.get("remaining_issues") or []) if str(r).strip()]
            if remaining:
                st.caption("Remaining issues: " + "; ".join(str(r) for r in remaining))
            if not improvements and not failing and not remaining:
                st.caption("_No detail recorded for this pass._")


# ── Final per-scorer breakdown (from proposal_scores) ───────────────


def _final_breakdown(proposal_id: str, section_id: str) -> None:
    detail = queries.proposal_division_scores(proposal_id, section_id)
    if detail.empty:
        st.info("No scores found for this division.")
        return

    st.caption(
        f"_{len(detail)} scorers · final state · sorted weakest-grade first._"
    )
    bands = st.multiselect(
        "Filter by grade",
        options=[1, 2, 3, 4, 5],
        default=[],
        help="Empty = show all grades",
        key=f"dd_grade_filter_{proposal_id}_{section_id}",
    )
    if bands:
        detail = detail[detail["grade"].isin(bands)]

    for _, r in detail.iterrows():
        scorer = label_for(r["scorer_id"])
        veto = " ⛔" if (r["is_veto"] and int(r["grade"]) <= 2) else ""
        cat = f" · {r['evaluator_category']}" if r["evaluator_category"] else ""
        with st.expander(f"{scorer}{veto}", expanded=False):
            st.markdown(_grade_pill(r["grade"], r["grade_label"]), unsafe_allow_html=True)
            reasoning = _clean(r["reasoning"])
            if reasoning:
                st.markdown("**Reasoning**")
                st.write(reasoning)
            kw = _clean(r["key_weakness"])
            if kw:
                st.markdown("**Key weakness**")
                st.write(kw)
            st.caption(
                f"Scorer: `{r['scorer_id']}`{cat}  ·  Model: "
                f"`{r['model'] or 'unknown'}`  ·  Redraft {int(r['revision_count'] or 0)}  ·  "
                f"Scored {r['scored_on']}"
            )


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    """Render the Proposal deep-dive tab. `f` is the sidebar Filters."""
    st.markdown("### Proposal deep-dive")
    st.caption(
        "Pick one AI-draft proposal and walk its full scoring structure — "
        "every EC division, how each climbed across redraft passes, and "
        "every scorer's final grade + justification per division."
    )

    listing = queries.scored_proposal_list(days=f.days)
    if listing.empty:
        st.info("No scored proposals in the selected window — widen the lookback in the sidebar.")
        return

    options = {
        row["id"]: (
            f"{str(row['title'])[:70]}  ·  avg {row['avg_grade']:.2f}  ·  "
            f"{int(row['divisions'])} divisions  ·  {int(row['scorers'])} scorers  ·  "
            f"{'↻ trajectory' if row['has_trajectory'] else 'final-only'}  ·  "
            f"{row['owner'] or '—'}"
        )
        for _, row in listing.iterrows()
    }
    picked = st.selectbox(
        "Pick a proposal",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        key="dd_proposal_pick",
    )
    if not picked:
        return

    meta = listing[listing["id"] == picked].iloc[0]
    cols = st.columns(5)
    cols[0].metric("Final avg grade", f"{meta['avg_grade']:.2f}")
    cols[1].metric("Divisions", int(meta["divisions"]))
    cols[2].metric("Scorers", int(meta["scorers"]))
    cols[3].metric("Max redrafts", int(meta["max_redraft"] or 0))
    cols[4].metric("Status", str(meta["status"] or "—"))

    # ── Division picker, grouped by parent EC section ────────────────
    summary = queries.proposal_division_summary(picked)
    if summary.empty:
        st.info("No division-level scores for this proposal.")
        return

    summary = summary.copy()
    summary["section"] = summary["section_id"].map(division_section)
    summary["sort_key"] = summary["section_id"].map(division_sort_key)
    summary = summary.sort_values("sort_key")

    div_options = {
        row["section_id"]: (
            f"{division_label(row['section_id'])}  ·  avg {row['avg_grade']:.2f}  ·  "
            f"{int(row['scorers'])} scorers  ·  {int(row['weak'])} weak (≤3)"
            + (f"  ·  {int(row['vetoes'])} veto" if int(row['vetoes']) else "")
        )
        for _, row in summary.iterrows()
    }
    picked_div = st.selectbox(
        "Pick a division",
        options=list(div_options.keys()),
        format_func=lambda k: div_options[k],
        key=f"dd_division_pick_{picked}",
    )
    if not picked_div:
        return

    section = division_section(picked_div)
    st.markdown(f"#### {division_label(picked_div)}")
    st.caption(f"EC section: **{section}**  ·  division id `{picked_div}`")

    # ── Redraft trajectory ───────────────────────────────────────────
    st.markdown("##### Redraft trajectory")
    sv = queries.proposal_section_versions(picked)
    _trajectory(picked_div, sv.get(picked_div, {}))

    # ── Final per-scorer breakdown ───────────────────────────────────
    st.markdown("##### Final per-scorer breakdown")
    _final_breakdown(picked, picked_div)
