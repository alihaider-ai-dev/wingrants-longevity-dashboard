"""
Proposal deep-dive tab — proposal → section → scorer × revision matrix.

The headline view is a colour-coded pivot table per section:
  - rows    = scorers (the criteria), weakest final grade first
  - columns = revisions (redraft passes), with the redraft TYPE in the
              header (full / targeted / targeted_compress / page_only)
  - cell    = the grade, coloured 1-BAD → 5-EXCELLENT

How the engine scores (the model the table encodes):
  - Only FAILING scorers are re-evaluated each revision; a scorer that
    already passed isn't re-scored — its grade carries forward (sticky).
  - So a cell is one of two things:
      • a fresh failing grade (bold, full colour) — the scorer was
        re-evaluated at that revision and still failing
        (from `section_versions.change_history`), OR
      • a carried-forward grade (faded) — the scorer had already passed
        and wasn't re-evaluated; we show its final/stuck grade.
  - Full reasoning exists only at the accepted revision (`proposal_scores`);
    failing cells carry the key weakness. Hover any cell to read it, or
    use the justification reader at the bottom for full text.
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

_TYPE_ABBR = {
    "full": "Full",
    "targeted": "Surgical",
    "targeted_compress": "Surgical+compress",
    "page_only": "Page-fit",
}


def _attr(s) -> str:
    """Escape a string for use inside an HTML attribute."""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


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


def _grade_pill(grade, grade_label=None) -> str:
    if grade is None:
        return "—"
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return "—"
    bg, fg = quality_color(g)
    label = (grade_label or _ql(g) or "").upper()
    return (f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:600;font-size:11px;'>{g} · {label}</span>")


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _best_revision(history: list) -> tuple | None:
    if not history:
        return None
    vals = [float(v) for v in history]
    best = max(vals)
    return (vals.index(best), best)


def _failing_map(change_history: list) -> dict:
    out = {}
    for ch in change_history:
        out[ch.get("revision")] = {f.get("id"): f for f in (ch.get("failing_at_entry") or [])}
    return out


def _int_revisions(change_history: list) -> list:
    return sorted(r for r in (ch.get("revision") for ch in change_history) if isinstance(r, int))


# ── Decimal score-across-revisions chart ────────────────────────────


def _trajectory_chart(history: list, key: str) -> None:
    vals = [float(v) for v in history]
    best_idx = vals.index(max(vals))
    last_idx = len(vals) - 1

    def kind(i):
        if i == best_idx and i == last_idx:
            return "Best & accepted"
        if i == best_idx:
            return "Best"
        if i == last_idx:
            return "Accepted (last)"
        return "Other"

    df = pd.DataFrame({"revision": list(range(len(vals))), "score": vals,
                       "kind": [kind(i) for i in range(len(vals))]})
    # Decimal-friendly zoomed y-domain so 4.1 vs 4.3 is visible.
    lo = max(0.0, min(vals) - 0.3)
    hi = min(5.0, max(vals) + 0.2)
    line = alt.Chart(df).mark_line(color="#B3ADC1", strokeWidth=2).encode(
        x=alt.X("revision:O", title="Revision (redraft pass)", axis=alt.Axis(labelColor=INK_SOFT)),
        y=alt.Y("score:Q", scale=alt.Scale(domain=[lo, hi], nice=False),
                title="Avg score",
                axis=alt.Axis(format=".1f", tickCount=6, grid=True, gridColor=RULE,
                              gridOpacity=0.6, labelColor=INK_SOFT)),
    )
    pts = alt.Chart(df).mark_point(filled=True, size=110).encode(
        x="revision:O", y="score:Q",
        color=alt.Color("kind:N",
                        scale=alt.Scale(domain=["Best", "Accepted (last)", "Best & accepted", "Other"],
                                        range=[MINT, ACCENT_DEEP, MINT, "#B3ADC1"]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("revision:O", title="Revision"),
                 alt.Tooltip("score:Q", title="Avg score", format=".2f"),
                 alt.Tooltip("kind:N", title="")],
    )
    st.altair_chart((line + pts).properties(height=190).configure_view(stroke=None),
                    use_container_width=True)


# ── The colour-coded scorer × revision table ────────────────────────


def _section_table(detail: pd.DataFrame, change_history: list, modes: dict) -> None:
    int_revs = _int_revisions(change_history)
    fmap = _failing_map(change_history)
    d = detail.sort_values(["grade", "scorer_id"])

    # Header
    head_cells = ["<th class='nm'>Scorer (criterion)</th>"]
    for r in int_revs:
        mode = modes.get(r)
        abbr = _TYPE_ABBR.get(mode, mode or "—")
        head_cells.append(f"<th>Rev {r}<br><span class='t'>{_attr(abbr)}</span></th>")
    head_cells.append("<th>Final</th>")

    body = []
    for _, row in d.iterrows():
        sid = row["scorer_id"]
        final = int(row["grade"])
        reason = _clean(row["reasoning"])
        name_title = _attr(f"Final grade {final}: {reason}" if reason else f"Final grade {final}")
        cells = [f"<td class='nm' title=\"{name_title}\">{_attr(label_for(sid))}</td>"]
        for r in int_revs:
            f = fmap.get(r, {}).get(sid)
            if f:  # re-evaluated, still failing → fresh grade
                g = int(f.get("grade"))
                bg, fg = quality_color(g)
                wk = _clean(f.get("key_weakness"))
                title = _attr(f"Rev {r} — re-evaluated, FAILING (grade {g}): {wk}")
                cells.append(f"<td style='background:{bg};color:{fg};font-weight:700' "
                             f"title=\"{title}\">{g}</td>")
            else:   # passed already → carried forward (faded)
                bg = _hex_to_rgba(quality_color(final)[0], 0.32)
                cells.append(f"<td style='background:{bg};color:#8A8398' "
                             f"title=\"carried forward — passed earlier, not re-evaluated\">{final}·</td>")
        fbg, ffg = quality_color(final)
        cells.append(f"<td style='background:{fbg};color:{ffg};font-weight:700'>{final}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    html = f"""
    <style>
      .revwrap {{ overflow:auto; max-height:540px; border:1px solid {RULE}; border-radius:8px; }}
      table.rev {{ border-collapse:collapse; font-size:11px; width:100%; }}
      table.rev th, table.rev td {{ border:1px solid {RULE}; padding:3px 7px; text-align:center; white-space:nowrap; }}
      table.rev th {{ position:sticky; top:0; background:#F4EEE7; color:#1A1530; font-weight:600; z-index:2; }}
      table.rev th .t {{ font-weight:400; color:#6D6682; font-size:9px; }}
      table.rev td.nm, table.rev th.nm {{ text-align:left; max-width:260px; overflow:hidden;
          text-overflow:ellipsis; position:sticky; left:0; background:#FBF7F1; z-index:1; }}
      table.rev th.nm {{ z-index:3; }}
    </style>
    <div class='revwrap'><table class='rev'>
      <thead><tr>{''.join(head_cells)}</tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table></div>
    """
    st.markdown(html, unsafe_allow_html=True)
    st.caption(
        "Bold cell = scorer was re-evaluated and **failing** that revision (hover for the weakness). "
        "Faded `n·` = **carried forward** (already passed, not re-evaluated). "
        "Colours: 1 BAD · 2 POOR · 3 FAIR · 4 GOOD · 5 EXCELLENT."
    )


# ── Justification reader (full text across revisions) ───────────────


def _justification_reader(proposal_id: str, summary: pd.DataFrame, sv: dict, key: str) -> None:
    sec_opts = {row["section_id"]: division_label(row["section_id"]) for _, row in summary.iterrows()}
    sec = st.selectbox("Section", options=list(sec_opts.keys()),
                       format_func=lambda k: sec_opts[k], key=f"jr_sec_{key}")
    detail = queries.proposal_division_scores(proposal_id, sec)
    if detail.empty:
        st.info("No scores for this section.")
        return
    d = detail.copy()
    d["lbl"] = d["scorer_id"].map(label_for)
    d = d.sort_values("grade")
    opts = {r["scorer_id"]: f"{int(r['grade'])} · {r['lbl']}" for _, r in d.iterrows()}
    picked = st.selectbox("Scorer", options=list(opts.keys()),
                          format_func=lambda k: opts[k], key=f"jr_scorer_{key}")
    row = d[d["scorer_id"] == picked].iloc[0]

    st.markdown("**Final / accepted revision**")
    st.markdown(_grade_pill(row["grade"], row["grade_label"]), unsafe_allow_html=True)
    if _clean(row["reasoning"]):
        st.markdown("**Reasoning**")
        st.write(_clean(row["reasoning"]))
    if _clean(row["key_weakness"]):
        st.markdown("**Key weakness**")
        st.write(_clean(row["key_weakness"]))

    change_history = (sv.get(sec) or {}).get("change_history") or []
    fmap = _failing_map(change_history)
    st.markdown("**Status at each evaluated revision**")
    revs = sorted((ch.get("revision") for ch in change_history),
                  key=lambda r: -1 if not isinstance(r, int) else r)
    if not revs:
        st.caption("_No per-revision history stored for this section._")
        return
    for r in revs:
        lbl = "Earlier revisions" if not isinstance(r, int) else f"Revision {r}"
        f = fmap.get(r, {}).get(picked)
        if f:
            wk = _clean(f.get("key_weakness"))
            st.markdown(
                f"{_grade_pill(f.get('grade'), f.get('grade_label'))} &nbsp; **{lbl}** — re-evaluated, failing"
                + (f"<br><span style='color:#6D6682;font-size:12px;'>{wk}</span>" if wk else ""),
                unsafe_allow_html=True)
        else:
            st.markdown(
                f"<span style='color:#1F4B1D;'>✓</span> &nbsp; **{lbl}** — "
                f"<span style='color:#6D6682;'>passing / not re-evaluated (carried forward)</span>",
                unsafe_allow_html=True)


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    st.markdown("### Proposal deep-dive")
    st.caption(
        "Pick a proposal to see, per section, a colour-coded scorer × "
        "revision table — how every scorer's grade moved across the redraft "
        "passes."
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

    st.info(
        "ℹ️ Only **failing** scorers are re-evaluated each revision — a scorer that already "
        "passed isn't re-scored, its grade carries forward. So bold cells are fresh failing "
        "grades; faded cells are carried forward.",
        icon="ℹ️",
    )

    summary = queries.proposal_division_summary(picked)
    if summary.empty:
        st.info("No section-level scores for this proposal.")
        return
    summary = summary.copy()
    summary["sort_key"] = summary["section_id"].map(division_sort_key)
    summary = summary.sort_values("sort_key").reset_index(drop=True)

    sv = queries.proposal_section_versions(picked)
    modes_df = queries.proposal_revision_modes(picked)
    modes_by_sec: dict = {}
    for _, m in modes_df.iterrows():
        modes_by_sec.setdefault(m["section_id"], {})[int(m["revision"])] = m["mode"]

    st.markdown("#### Scores by revision")
    for i, srow in summary.iterrows():
        sid = srow["section_id"]
        sv_entry = sv.get(sid, {})
        history = sv_entry.get("score_history") or []
        change_history = sv_entry.get("change_history") or []
        best = _best_revision(history)
        best_txt = ""
        if best:
            best_txt = f"  ·  best rev #{best[0]} ({best[1]:.2f})"
        header = (f"{division_label(sid)}  ·  avg {srow['avg_grade']:.2f}  ·  "
                  f"{int(srow['scorers'])} scorers  ·  {int(srow['max_redraft'] or 0)} revisions{best_txt}")
        with st.expander(header, expanded=(i == 0)):
            if history:
                _trajectory_chart(history, key=f"{picked}_{sid}")
            detail = queries.proposal_division_scores(picked, sid)
            if detail.empty:
                st.caption("No scores for this section.")
            else:
                _section_table(detail, change_history, modes_by_sec.get(sid, {}))

    st.markdown("---")
    st.markdown("#### Read a scorer's justifications across revisions")
    _justification_reader(picked, summary, sv, key=picked)
