"""
Proposal deep-dive tab — proposal → section → revisions / scorers.

Pick one AI-draft proposal and one EC section, then read:
  1. Score across revisions — the per-revision score line with the BEST
     revision marked (the engine doesn't always end on the best one).
  2. Top scorers — every scorer's grade + full reasoning for the
     accepted revision, sortable weakest- or strongest-first.
  3. One scorer across revisions — pick a scorer and follow their grade +
     key weakness from revision to revision.

Data reality (verified against the DB):
  - `proposal_scores` keeps ONE row per (section, scorer) — the ACCEPTED
    revision. Full reasoning lives here, once per scorer.
  - `proposals.section_versions` keeps, per revision, only the average
    score (`score_history`) and the FAILING scorers' id+grade+key_weakness
    (`change_history`). No full reasoning, no passing scorers per revision.
  So the accepted revision shows everything; other revisions show the
  score trend + which scorers were failing and why.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from src import queries
from src.charts import ACCENT_DEEP, INK_SOFT, MINT, RULE
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


def _best_revision(history: list) -> tuple | None:
    """(revision_count, score) of the highest-scoring revision.

    `score_history` is indexed by revision_count (index 0 == the initial
    draft, revision 0), so the list position IS the revision number.
    """
    if not history:
        return None
    vals = [float(v) for v in history]
    best = max(vals)
    return (vals.index(best), best)


# ── Score across revisions (line, best + accepted marked) ───────────


def _trajectory_chart(history: list) -> None:
    vals = [float(v) for v in history]
    best_idx = vals.index(max(vals))
    last_idx = len(vals) - 1  # the accepted revision is the last pass

    def kind(i: int) -> str:
        if i == best_idx and i == last_idx:
            return "Best & accepted"
        if i == best_idx:
            return "Best"
        if i == last_idx:
            return "Accepted"
        return "Other"

    df = pd.DataFrame({
        "revision": list(range(len(vals))),   # 0-based == revision_count
        "score": vals,
        "kind": [kind(i) for i in range(len(vals))],
    })

    line = alt.Chart(df).mark_line(color="#B3ADC1", strokeWidth=2).encode(
        x=alt.X("revision:O", title="Revision (redraft pass)", axis=alt.Axis(labelColor=INK_SOFT)),
        y=alt.Y(
            "score:Q",
            scale=alt.Scale(domain=[0, 5]),
            title="Avg score",
            axis=alt.Axis(values=[0, 1, 2, 3, 4, 5], grid=True,
                          gridColor=RULE, gridOpacity=0.6, labelColor=INK_SOFT),
        ),
    )
    points = alt.Chart(df).mark_point(filled=True, size=120).encode(
        x="revision:O",
        y="score:Q",
        color=alt.Color(
            "kind:N",
            scale=alt.Scale(
                domain=["Best", "Accepted", "Best & accepted", "Other"],
                range=[MINT, ACCENT_DEEP, MINT, "#B3ADC1"],
            ),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=[
            alt.Tooltip("revision:O", title="Revision"),
            alt.Tooltip("score:Q", title="Avg score", format=".2f"),
            alt.Tooltip("kind:N", title=""),
        ],
    )
    st.altair_chart((line + points).properties(height=200).configure_view(stroke=None),
                    use_container_width=True)


# ── Top scorers (accepted revision) ─────────────────────────────────


def _top_scorers(detail: pd.DataFrame, key: str) -> None:
    order = st.radio(
        "Sort",
        options=["Weakest first", "Strongest first"],
        horizontal=True,
        key=f"dd_sort_{key}",
        label_visibility="collapsed",
    )
    detail = detail.sort_values("grade", ascending=(order == "Weakest first"))

    for _, r in detail.iterrows():
        veto = " ⛔" if (r["is_veto"] and int(r["grade"]) <= 2) else ""
        header = f"{int(r['grade'])} · {label_for(r['scorer_id'])}{veto}"
        with st.expander(header, expanded=False):
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


# ── One scorer across revisions ─────────────────────────────────────


def _scorer_across_revisions(detail: pd.DataFrame, sv_entry: dict, accepted_rev, key: str) -> None:
    detail = detail.copy()
    detail["label"] = detail["scorer_id"].map(label_for)
    detail = detail.sort_values("scorer_id")
    opts = {row["scorer_id"]: f"{row['label']}  ·  final grade {int(row['grade'])}"
            for _, row in detail.iterrows()}
    picked = st.selectbox(
        "Pick a scorer",
        options=list(opts.keys()),
        format_func=lambda k: opts[k],
        key=f"dd_scorer_pick_{key}",
    )
    if not picked:
        return
    row = detail[detail["scorer_id"] == picked].iloc[0]

    # Accepted-revision reasoning (the one revision with full text).
    st.markdown(
        f"**Final / accepted revision {int(row['revision_count'] or accepted_rev or 0)}**",
    )
    st.markdown(_grade_pill(row["grade"], row["grade_label"]), unsafe_allow_html=True)
    reasoning = _clean(row["reasoning"])
    if reasoning:
        st.markdown("**Reasoning**")
        st.write(reasoning)
    kw = _clean(row["key_weakness"])
    if kw:
        st.markdown("**Key weakness**")
        st.write(kw)

    # Earlier revisions where this scorer was failing (key weakness only).
    changes = sv_entry.get("change_history") or []
    hits = []
    for entry in changes:
        for f in (entry.get("failing_at_entry") or []):
            if f.get("id") == picked:
                hits.append((entry.get("revision"), f))
    st.markdown("**Across earlier revisions**")
    if not hits:
        st.caption(
            "_This scorer wasn't recorded as failing in any earlier revision — "
            "the engine only stores per-revision detail for failing scorers, so "
            "there's nothing earlier to show for a scorer that stayed above the bar._"
        )
        return
    for rev, f in hits:
        rev_label = f"Revision {rev}" if isinstance(rev, int) else str(rev or "earlier").title()
        pill = _grade_pill(f.get("grade"), f.get("grade_label"))
        kw = _clean(f.get("key_weakness"))
        st.markdown(
            f"{pill} &nbsp; **{rev_label}**"
            + (f"<br><span style='color:#6D6682;font-size:12px;'>{kw}</span>" if kw else ""),
            unsafe_allow_html=True,
        )


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    st.markdown("### Proposal deep-dive")
    st.caption(
        "Pick a proposal and a section to see how its score moved across "
        "revisions, the top scorers, and any single scorer's journey."
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
    meta = listing[listing["id"] == picked].iloc[0]
    cols = st.columns(3)
    cols[0].metric("Avg grade", f"{meta['avg_grade']:.2f}")
    cols[1].metric("Sections", int(meta["divisions"]))
    cols[2].metric("Scorers", int(meta["scorers"]))

    # ── Section picker (auto-loads first section) ────────────────────
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
    srow = summary[summary["section_id"] == picked_sec].iloc[0]
    accepted_rev = int(srow["max_redraft"] or 0)

    sv = queries.proposal_section_versions(picked)
    sv_entry = sv.get(picked_sec, {})
    history = sv_entry.get("score_history") or []
    best = _best_revision(history)

    # ── Section header — best vs accepted, stated plainly ────────────
    st.markdown(f"#### {division_label(picked_sec)}")
    if best:
        best_rev, best_score = best
        note = f"Best-scoring revision: **#{best_rev}** (score {best_score:.2f}). "
        if best_rev == accepted_rev:
            note += f"Per-scorer detail below is the **accepted revision #{accepted_rev}** — also the best."
        else:
            note += (
                f"Per-scorer detail below is the **accepted revision #{accepted_rev}** — "
                f"the engine ran out of revisions before its best draft, so they differ."
            )
        st.caption(note)
    else:
        st.caption(f"Accepted revision **#{accepted_rev}** · no per-revision trend stored for this section.")

    # ── Score across revisions ───────────────────────────────────────
    if history:
        st.markdown("##### Score across revisions")
        _trajectory_chart(history)

    # ── Scorers — two modes ──────────────────────────────────────────
    detail = queries.proposal_division_scores(picked, picked_sec)
    if detail.empty:
        st.info("No scores found for this section.")
        return

    mode = st.radio(
        "View",
        options=["Top scorers", "One scorer across revisions"],
        horizontal=True,
        key=f"dd_mode_{picked}_{picked_sec}",
    )
    st.caption(f"_{len(detail)} scorers · accepted revision #{accepted_rev}._")
    if mode == "Top scorers":
        _top_scorers(detail, key=f"{picked}_{picked_sec}")
    else:
        _scorer_across_revisions(detail, sv_entry, accepted_rev, key=f"{picked}_{picked_sec}")
