"""
Proposal deep-dive tab — proposal → section → revisions.

How the engine scores (the mental model the views are built around):
  - Each section is drafted, scored by every scorer, then redrafted and
    re-scored — but ONLY the scorers that are still failing get re-run.
    A scorer that already passed is NOT re-evaluated; its grade carries
    forward ("sticky"). So per revision, the only fresh scores are the
    failing ones — that's exactly what `section_versions.change_history`
    stores (the failing scorers' id · grade · key_weakness per revision).
  - `proposal_scores` holds each scorer's grade at the ACCEPTED revision
    (the last pass), with full reasoning.

Three views, all driven off that:
  • By scorer    — pick a scorer, see its status at every evaluated
                   revision (failing → grade+weakness, otherwise passing /
                   carried forward) plus its full accepted reasoning.
  • By score     — pick a grade (or "failing ≤3"), see every scorer at it.
  • By revision  — pick a revision, see what was failing then + the score.
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
    """(revision_count, score) of the best revision. score_history is
    indexed by revision_count, so list position == revision number."""
    if not history:
        return None
    vals = [float(v) for v in history]
    best = max(vals)
    return (vals.index(best), best)


def _rev_sort_key(rev) -> int:
    """Order change_history revisions: 'earlier' first, then ints."""
    return -1 if not isinstance(rev, int) else rev


def _evaluated_revisions(change_history: list) -> list:
    """Revisions (in order) that actually have a change_history entry."""
    return sorted((ch.get("revision") for ch in change_history), key=_rev_sort_key)


def _failing_map(change_history: list) -> dict:
    """{revision: {scorer_id: failing_obj}} from change_history."""
    out = {}
    for ch in change_history:
        rev = ch.get("revision")
        out[rev] = {f.get("id"): f for f in (ch.get("failing_at_entry") or [])}
    return out


# ── Score-across-revisions chart (best + accepted marked) ───────────


def _trajectory_chart(history: list) -> None:
    vals = [float(v) for v in history]
    best_idx = vals.index(max(vals))
    last_idx = len(vals) - 1

    def kind(i: int) -> str:
        if i == best_idx and i == last_idx:
            return "Best & accepted"
        if i == best_idx:
            return "Best"
        if i == last_idx:
            return "Accepted (last)"
        return "Other"

    df = pd.DataFrame({
        "revision": list(range(len(vals))),
        "score": vals,
        "kind": [kind(i) for i in range(len(vals))],
    })
    line = alt.Chart(df).mark_line(color="#B3ADC1", strokeWidth=2).encode(
        x=alt.X("revision:O", title="Revision (redraft pass)", axis=alt.Axis(labelColor=INK_SOFT)),
        y=alt.Y("score:Q", scale=alt.Scale(domain=[0, 5]), title="Avg score",
                axis=alt.Axis(values=[0, 1, 2, 3, 4, 5], grid=True, gridColor=RULE,
                              gridOpacity=0.6, labelColor=INK_SOFT)),
    )
    points = alt.Chart(df).mark_point(filled=True, size=120).encode(
        x="revision:O", y="score:Q",
        color=alt.Color("kind:N",
                        scale=alt.Scale(domain=["Best", "Accepted (last)", "Best & accepted", "Other"],
                                        range=[MINT, ACCENT_DEEP, MINT, "#B3ADC1"]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("revision:O", title="Revision"),
                 alt.Tooltip("score:Q", title="Avg score", format=".2f"),
                 alt.Tooltip("kind:N", title="")],
    )
    st.altair_chart((line + points).properties(height=200).configure_view(stroke=None),
                    use_container_width=True)


# ── View: By scorer (status at every evaluated revision) ────────────


def _by_scorer(detail: pd.DataFrame, change_history: list, accepted_rev: int, key: str) -> None:
    d = detail.copy()
    d["label"] = d["scorer_id"].map(label_for)
    d = d.sort_values("grade")  # weakest first so problem scorers are easy to find
    opts = {r["scorer_id"]: f"{int(r['grade'])} · {r['label']}" for _, r in d.iterrows()}
    picked = st.selectbox("Pick a scorer", options=list(opts.keys()),
                          format_func=lambda k: opts[k], key=f"dd_scorer_{key}")
    if not picked:
        return
    row = d[d["scorer_id"] == picked].iloc[0]

    # Accepted-revision result (the one revision with full reasoning).
    st.markdown(f"**Accepted revision #{accepted_rev} — final result**")
    st.markdown(_grade_pill(row["grade"], row["grade_label"]), unsafe_allow_html=True)
    reasoning = _clean(row["reasoning"])
    if reasoning:
        st.markdown("**Reasoning**")
        st.write(reasoning)
    kw = _clean(row["key_weakness"])
    if kw:
        st.markdown("**Key weakness**")
        st.write(kw)

    # Revision-by-revision status, using the failing history.
    st.markdown("**Status at each evaluated revision**")
    fmap = _failing_map(change_history)
    revs = _evaluated_revisions(change_history)
    if not revs:
        st.caption("_No per-revision history stored for this section._")
        return
    for rev in revs:
        rev_label = "Earlier revisions" if not isinstance(rev, int) else f"Revision {rev}"
        fobj = fmap.get(rev, {}).get(picked)
        if fobj:
            pill = _grade_pill(fobj.get("grade"), fobj.get("grade_label"))
            wk = _clean(fobj.get("key_weakness"))
            st.markdown(
                f"{pill} &nbsp; **{rev_label}** — re-evaluated, still failing"
                + (f"<br><span style='color:#6D6682;font-size:12px;'>{wk}</span>" if wk else ""),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<span style='color:#1F4B1D;'>✓</span> &nbsp; **{rev_label}** — "
                f"<span style='color:#6D6682;'>passing / not re-evaluated (grade carried forward)</span>",
                unsafe_allow_html=True,
            )


# ── View: By score (every scorer at a chosen grade) ─────────────────


def _by_score(detail: pd.DataFrame, change_history: list, key: str) -> None:
    counts = detail["grade"].value_counts().to_dict()
    grade_opts = ["Failing (≤ 3)"] + [
        f"Grade {g} · {_ql(g)} ({counts.get(g, 0)})" for g in [1, 2, 3, 4, 5] if counts.get(g)
    ]
    choice = st.selectbox("Show scorers at", options=grade_opts, key=f"dd_score_{key}")
    if choice.startswith("Failing"):
        sub = detail[detail["grade"] <= 3].sort_values("grade")
    else:
        g = int(choice.split()[1])
        sub = detail[detail["grade"] == g].sort_values("scorer_id")

    if sub.empty:
        st.caption("_No scorers in this band._")
        return
    fmap = _failing_map(change_history)
    st.caption(f"_{len(sub)} scorers._")
    for _, r in sub.iterrows():
        # how many revisions this scorer was failing in
        n_fail = sum(1 for rev in fmap if r["scorer_id"] in fmap[rev])
        suffix = f"  ·  failed in {n_fail} revision(s)" if n_fail else ""
        with st.expander(f"{int(r['grade'])} · {label_for(r['scorer_id'])}{suffix}", expanded=False):
            st.markdown(_grade_pill(r["grade"], r["grade_label"]), unsafe_allow_html=True)
            reasoning = _clean(r["reasoning"])
            if reasoning:
                st.markdown("**Reasoning**")
                st.write(reasoning)
            kw = _clean(r["key_weakness"])
            if kw:
                st.markdown("**Key weakness**")
                st.write(kw)


# ── View: By revision (what was failing at a chosen revision) ───────


def _by_revision(change_history: list, accepted_rev: int, key: str) -> None:
    if not change_history:
        st.caption("_No per-revision history stored for this section._")
        return
    by_rev = {ch.get("revision"): ch for ch in change_history}
    revs = _evaluated_revisions(change_history)
    labels = {rev: ("Earlier revisions" if not isinstance(rev, int) else f"Revision {rev}")
              + (" · accepted" if rev == accepted_rev else "") for rev in revs}
    picked = st.selectbox("Pick a revision", options=revs,
                          format_func=lambda r: labels[r], index=len(revs) - 1,
                          key=f"dd_rev_{key}")
    ch = by_rev[picked]
    score = ch.get("score")
    failing = ch.get("failing_at_entry") or []
    cols = st.columns(2)
    cols[0].metric("Avg score at this revision", f"{float(score):.2f}" if score is not None else "—")
    cols[1].metric("Scorers still failing", len(failing))

    improvements = [i for i in (ch.get("improvements_made") or []) if str(i).strip()]
    if improvements:
        st.markdown("**What changed going into this revision**")
        for imp in improvements:
            st.markdown(f"- {imp}")

    if failing:
        st.markdown("**Failing scorers at this revision**")
        for f in sorted(failing, key=lambda x: x.get("grade", 9)):
            pill = _grade_pill(f.get("grade"), f.get("grade_label"))
            wk = _clean(f.get("key_weakness"))
            st.markdown(
                f"{pill} &nbsp; **{label_for(f.get('id'))}**"
                + (f"<br><span style='color:#6D6682;font-size:12px;'>{wk}</span>" if wk else ""),
                unsafe_allow_html=True,
            )
        st.caption("Every other scorer had already passed and was **not re-evaluated** at this revision.")
    else:
        st.success("No scorers failing at this revision — every scorer was passing or already carried forward.")


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    st.markdown("### Proposal deep-dive")
    st.caption(
        "Pick a proposal and a section to follow its scoring across redraft "
        "revisions — by scorer, by grade, or by revision."
    )

    listing = queries.scored_proposal_list(days=f.days)
    if listing.empty:
        st.info("No scored proposals in the selected window — widen the lookback in the sidebar.")
        return

    options = {row["id"]: f"{str(row['title'])[:72]}  ·  avg {row['avg_grade']:.2f}"
               for _, row in listing.iterrows()}
    picked = st.selectbox("Pick a proposal", options=list(options.keys()),
                          format_func=lambda k: options[k], key="dd_proposal_pick")
    meta = listing[listing["id"] == picked].iloc[0]
    cols = st.columns(3)
    cols[0].metric("Avg grade", f"{meta['avg_grade']:.2f}")
    cols[1].metric("Sections", int(meta["divisions"]))
    cols[2].metric("Scorers", int(meta["scorers"]))

    summary = queries.proposal_division_summary(picked)
    if summary.empty:
        st.info("No section-level scores for this proposal.")
        return
    summary = summary.copy()
    summary["sort_key"] = summary["section_id"].map(division_sort_key)
    summary = summary.sort_values("sort_key")
    sec_options = {row["section_id"]: f"{division_label(row['section_id'])}  ·  avg {row['avg_grade']:.2f}  ·  "
                                      f"{int(row['scorers'])} scorers"
                   for _, row in summary.iterrows()}
    picked_sec = st.selectbox("Pick a section", options=list(sec_options.keys()),
                              format_func=lambda k: sec_options[k], key=f"dd_section_pick_{picked}")
    srow = summary[summary["section_id"] == picked_sec].iloc[0]
    accepted_rev = int(srow["max_redraft"] or 0)

    sv = queries.proposal_section_versions(picked)
    sv_entry = sv.get(picked_sec, {})
    history = sv_entry.get("score_history") or []
    change_history = sv_entry.get("change_history") or []
    best = _best_revision(history)

    st.markdown(f"#### {division_label(picked_sec)}")
    if best:
        best_rev, best_score = best
        note = f"Best-scoring revision: **#{best_rev}** (score {best_score:.2f}). "
        if best_rev == accepted_rev:
            note += f"Full per-scorer detail is the **accepted revision #{accepted_rev}** — also the best."
        else:
            note += (f"Full per-scorer detail is the **accepted revision #{accepted_rev}** — the engine "
                     f"ran out of revisions before its best draft, so they differ.")
        st.caption(note)

    # The behaviour to convey (point 4).
    st.info(
        "ℹ️ Only **failing** scorers are re-evaluated each revision. A scorer that already "
        "passed is **not re-scored** — its grade carries forward. So per revision you see the "
        "scorers that were still failing; everyone else had already passed.",
        icon="ℹ️",
    )

    if history:
        st.markdown("##### Score across revisions")
        _trajectory_chart(history)

    detail = queries.proposal_division_scores(picked, picked_sec)
    if detail.empty:
        st.info("No scores found for this section.")
        return

    mode = st.radio("View", options=["By scorer", "By score", "By revision"],
                    horizontal=True, key=f"dd_mode_{picked}_{picked_sec}")
    if mode == "By scorer":
        _by_scorer(detail, change_history, accepted_rev, key=f"{picked}_{picked_sec}")
    elif mode == "By score":
        _by_score(detail, change_history, key=f"{picked}_{picked_sec}")
    else:
        _by_revision(change_history, accepted_rev, key=f"{picked}_{picked_sec}")
