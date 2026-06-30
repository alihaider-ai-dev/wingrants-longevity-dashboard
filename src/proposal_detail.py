"""
Proposal deep-dive tab — ONE consolidated scorer × revision table.

Layout (a single table for the whole proposal):
  - Column 1  : Section  — merged (rowspan) down all its scorers.
  - Column 2  : Scorer   — merged (rowspan 2) across its two rows.
  - Columns 3+: Revision 1 … last redraft pass, then a Final column.
  - Each scorer occupies TWO rows:
        top row    = the grade at each revision
        bottom row = the justification / reason at each revision

How the engine scores (what the cells encode):
  - Only FAILING scorers are re-evaluated each revision; a scorer that
    already passed isn't re-scored — its grade carries forward (sticky).
  - So a grade cell is one of:
        • bold, full colour  → re-evaluated and failing that revision
          (grade from `section_versions.change_history`; the bottom cell
          shows that revision's key weakness), OR
        • faded `n·`         → carried forward (passed, not re-evaluated),
        • blank grey         → the section had fewer revisions than this.
  - The Final column is the accepted grade; its justification cell holds
    the full reasoning (only the accepted revision keeps full text).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src import queries
from src.charts import RULE
from src.proposal_sections import division_label, division_sort_key
from src.quality import quality_color
from src.scorer_names import label_for


def _attr(s) -> str:
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
    s = s.replace("\r\n", " ").replace("\n", " ")
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _failing_map(change_history: list) -> dict:
    out = {}
    for ch in change_history:
        out[ch.get("revision")] = {f.get("id"): f for f in (ch.get("failing_at_entry") or [])}
    return out


# ── The one consolidated table ──────────────────────────────────────


def _one_table(proposal_id: str, summary: pd.DataFrame, sv: dict, modes_by_sec: dict) -> None:
    # Gather each section's scorers + failing history + revision ceiling.
    sections, global_max = [], 0
    for _, srow in summary.iterrows():
        sid = srow["section_id"]
        detail = queries.proposal_division_scores(proposal_id, sid).sort_values(["grade", "scorer_id"])
        ch = (sv.get(sid) or {}).get("change_history") or []
        sec_max = int(srow["max_redraft"] or 0)
        global_max = max(global_max, sec_max)
        sections.append((sid, detail, _failing_map(ch), sec_max))

    rev_cols = list(range(1, global_max + 1))

    heads = ["<th class='sec'>Section</th>", "<th class='scr'>Scorer</th>"]
    heads += [f"<th>Rev {r}</th>" for r in rev_cols]
    heads.append("<th class='fin'>Final</th>")

    body = []
    for sid, detail, fmap, sec_max in sections:
        if detail.empty:
            continue
        modes = modes_by_sec.get(sid, {})
        sec_span = 2 * len(detail)
        sec_label = _attr(division_label(sid))

        for idx, (_, sr) in enumerate(detail.iterrows()):
            scid = sr["scorer_id"]
            final = int(sr["grade"])

            # ── Score row ──
            tds = []
            if idx == 0:
                tds.append(f"<td class='sec' rowspan='{sec_span}'>{sec_label}</td>")
            tds.append(f"<td class='scr' rowspan='2' title=\"{_attr(label_for(scid))}\">"
                       f"{_attr(label_for(scid))}</td>")
            for r in rev_cols:
                if r > sec_max:
                    tds.append("<td class='na'></td>")
                elif scid in fmap.get(r, {}):
                    g = int(fmap[r][scid].get("grade"))
                    bg, fg = quality_color(g)
                    tip = _attr(f"Rev {r} ({modes.get(r, '—')}) — re-evaluated, failing · grade {g}")
                    tds.append(f"<td class='sc' style='background:{bg};color:{fg};font-weight:700' "
                               f"title=\"{tip}\">{g}</td>")
                else:
                    bg = _hex_to_rgba(quality_color(final)[0], 0.30)
                    tds.append(f"<td class='sc' style='background:{bg};color:#8A8398' "
                               f"title='carried forward — passed earlier, not re-evaluated'>{final}·</td>")
            fbg, ffg = quality_color(final)
            tds.append(f"<td class='sc fin' style='background:{fbg};color:{ffg};font-weight:700'>{final}</td>")
            body.append(f"<tr>{''.join(tds)}</tr>")

            # ── Justification row ──
            jds = []
            for r in rev_cols:
                if r > sec_max:
                    jds.append("<td class='na'></td>")
                elif scid in fmap.get(r, {}):
                    wk = _clean(fmap[r][scid].get("key_weakness"))
                    jds.append(f"<td class='jt' title=\"{_attr(wk)}\">{_attr(wk[:240])}</td>")
                else:
                    jds.append("<td class='jt'></td>")
            reason = _clean(sr["reasoning"])
            jds.append(f"<td class='jt fin' title=\"{_attr(reason)}\">{_attr(reason[:340])}</td>")
            body.append(f"<tr>{''.join(jds)}</tr>")

    html = f"""
    <style>
      .onewrap {{ overflow:auto; max-height:640px; border:1px solid {RULE}; border-radius:8px; }}
      table.one {{ border-collapse:collapse; font-size:11px; }}
      table.one th, table.one td {{ border:1px solid {RULE}; padding:3px 6px; vertical-align:top; }}
      table.one th {{ position:sticky; top:0; z-index:4; background:#F4EEE7; color:#1A1530;
                      font-weight:600; text-align:center; white-space:nowrap; }}
      table.one td.sec {{ position:sticky; left:0; z-index:2; background:#F1E9DD; font-weight:700;
                          writing-mode:vertical-rl; transform:rotate(180deg); text-align:center;
                          white-space:nowrap; max-width:30px; color:#1A1530; }}
      table.one th.sec {{ left:0; z-index:5; }}
      table.one td.scr {{ position:sticky; left:30px; z-index:2; background:#FBF7F1; text-align:left;
                          width:185px; max-width:185px; font-size:10px; color:#1A1530; }}
      table.one th.scr {{ left:30px; z-index:5; }}
      table.one td.sc {{ text-align:center; vertical-align:middle; font-size:13px; min-width:46px; }}
      table.one td.jt {{ white-space:normal; font-size:9.5px; line-height:1.25; color:#3F3957;
                         background:#FCFAF6; min-width:150px; max-width:200px; }}
      table.one td.na {{ background:#F4EEE7; }}
      table.one td.fin, table.one th.fin {{ border-left:2px solid #B3ADC1; }}
      table.one td.jt.fin {{ max-width:300px; }}
    </style>
    <div class='onewrap'><table class='one'>
      <thead><tr>{''.join(heads)}</tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table></div>
    """
    st.markdown(html, unsafe_allow_html=True)
    st.caption(
        "Each scorer = two rows: **score** on top, **justification** beneath. "
        "Bold cell = re-evaluated & failing that revision · faded `n·` = carried forward "
        "(passed, not re-evaluated) · grey = section had fewer revisions. "
        "Colours: 1 BAD · 2 POOR · 3 FAIR · 4 GOOD · 5 EXCELLENT. Hover any cell for full text."
    )


# ── Tab entry point ─────────────────────────────────────────────────


def render(f) -> None:
    st.markdown("### Proposal deep-dive")
    st.caption(
        "One table per proposal: section → scorer → score (top row) and "
        "justification (bottom row) across every revision."
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

    _one_table(picked, summary, sv, modes_by_sec)
