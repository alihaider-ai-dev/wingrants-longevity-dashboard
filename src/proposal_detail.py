"""
Proposal deep-dive tab — proposal → section → revisions.

Pick one AI-draft proposal, pick one EC section, and see:
  1. Score across revisions — how that section's score moved redraft to
     redraft (read from `proposals.section_versions`), plus the scorers
     that were still failing at each revision.
  2. Scorers & justifications — every scorer's final grade + full
     reasoning + key weakness for that section (from `proposal_scores`),
     weakest-first.

Vocabulary note: a "section" here is the EC section_id stored on
`proposal_scores` (excellence_1_1, impact_2_2, _proposal_wide, …). A
"revision" is one redraft pass. (The helper functions in
`proposal_sections.py` still carry `division_*` names internally — same
concept, just the older label.)

Data note: `proposal_scores` only retains the FINAL revision per
(section, scorer), so full per-scorer reasoning exists once per scorer.
The per-revision story (score per pass + which scorers were failing)
comes from `section_versions`, which only keeps the failing subset at
each pass.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from src import queries
from src.charts import ACCENT_DEEP, INK, INK_SOFT, RULE
from src.proposal_sections import division_label, division_sort_key
from src.quality import quality_color
from src.quality import quality_label as _ql
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


# ── Score across revisions (from section_versions) ──────────────────


def _score_across_revisions(sv_entry: dict) -> None:
    """Line of avg score per revision + the failing scorers at each."""
    history = sv_entry.get("score_history") or []
    changes = sv_entry.get("change_history") or []

    if not history and not changes:
        st.caption("_No revision history stored for this section — showing the final revision only._")
        return

    if history:
        traj = pd.DataFrame({
            "revision": list(range(1, len(history) + 1)),
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
                x=alt.X("revision:O", title="Revision", axis=alt.Axis(labelColor=INK_SOFT)),
                y=alt.Y(
                    "score:Q",
                    scale=alt.Scale(domain=[0, 5]),
                    title="Avg score",
                    axis=alt.Axis(values=[0, 1, 2, 3, 4, 5], grid=True,
                                  gridColor=RULE, gridOpacity=0.6, labelColor=INK_SOFT),
                ),
                tooltip=[
                    alt.Tooltip("revision:O", title="Revision"),
                    alt.Tooltip("score:Q", title="Avg score", format=".2f"),
                ],
            )
            .properties(height=200)
            .configure_view(stroke=None)
        )
        st.altair_chart(line, use_container_width=True)

    # Compact per-revision failing-scorer list — the only per-revision,
    # per-scorer signal the data keeps. Revisions with nothing failing
    # are skipped so the section stays quiet once it's clean.
    rows = [e for e in changes if (e.get("failing_at_entry") or [])]
    if rows:
        st.markdown("**Failing scorers by revision**")
        for entry in rows:
            rev = entry.get("revision")
            if entry.get("_summary_of_earlier_attempts"):
                rev_label = "Earlier revisions"
            elif isinstance(rev, int):
                rev_label = f"Revision {rev}"
            else:
                rev_label = str(rev or "—").title()
            score = entry.get("score")
            score_str = f" · {float(score):.2f}" if score is not None else ""
            failing = entry.get("failing_at_entry") or []
            with st.expander(f"{rev_label}{score_str}  ·  {len(failing)} failing", expanded=False):
                for fobj in failing:
                    pill = _grade_pill(fobj.get("grade"), fobj.get("grade_label"))
                    kw = _clean(fobj.get("key_weakness"))
                    st.markdown(
                        f"{pill} &nbsp; **{label_for(fobj.get('id'))}**"
                        + (f"<br><span style='color:#6D6682;font-size:12px;'>{kw}</span>" if kw else ""),
                        unsafe_allow_html=True,
                    )


# ── Scorers & justifications (final revision, from proposal_scores) ──


def _final_breakdown(proposal_id: str, section_id: str) -> None:
    detail = queries.proposal_division_scores(proposal_id, section_id)
    if detail.empty:
        st.info("No scores found for this section.")
        return

    st.caption(f"_{len(detail)} scorers · final revision · weakest grade first._")
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
        veto = " ⛔" if (r["is_veto"] and int(r["grade"]) <= 2) else ""
        with st.expander(f"{label_for(r['scorer_id'])}{veto}", expanded=False):
            st.markdown(_grade_pill(r["grade"], r["grade_label"]), unsafe_allow_html=True)
            reasoning = _clean(r["reasoning"])
            if reasoning:
                st.markdown("**Reasoning**")
                st.write(reasoning)
            kw = _clean(r["key_weakness"])
            if kw:
                st.markdown("**Key weakness**")
                st.write(kw)
            st.caption(f"`{r['scorer_id']}`  ·  model `{r['model'] or 'unknown'}`  ·  scored {r['scored_on']}")


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    """Render the Proposal deep-dive tab. `f` is the sidebar Filters."""
    st.markdown("### Proposal deep-dive")
    st.caption(
        "Pick a proposal, then a section, to see its score across "
        "revisions and every scorer's final grade + justification."
    )

    listing = queries.scored_proposal_list(days=f.days)
    if listing.empty:
        st.info("No scored proposals in the selected window — widen the lookback in the sidebar.")
        return

    options = {
        row["id"]: f"{str(row['title'])[:72]}  ·  avg {row['avg_grade']:.2f}"
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
    cols = st.columns(3)
    cols[0].metric("Avg grade", f"{meta['avg_grade']:.2f}")
    cols[1].metric("Sections", int(meta["divisions"]))
    cols[2].metric("Revisions", int(meta["max_redraft"] or 0))

    # ── Section picker ───────────────────────────────────────────────
    summary = queries.proposal_division_summary(picked)
    if summary.empty:
        st.info("No section-level scores for this proposal.")
        return

    summary = summary.copy()
    summary["sort_key"] = summary["section_id"].map(division_sort_key)
    summary = summary.sort_values("sort_key")

    sec_options = {
        row["section_id"]: (
            f"{division_label(row['section_id'])}  ·  avg {row['avg_grade']:.2f}  ·  "
            f"{int(row['scorers'])} scorers"
        )
        for _, row in summary.iterrows()
    }
    picked_sec = st.selectbox(
        "Pick a section",
        options=list(sec_options.keys()),
        format_func=lambda k: sec_options[k],
        key=f"dd_section_pick_{picked}",
    )
    if not picked_sec:
        return

    st.markdown(f"#### {division_label(picked_sec)}")

    st.markdown("##### Score across revisions")
    sv = queries.proposal_section_versions(picked)
    _score_across_revisions(sv.get(picked_sec, {}))

    st.markdown("##### Scorers & justifications")
    _final_breakdown(picked, picked_sec)
