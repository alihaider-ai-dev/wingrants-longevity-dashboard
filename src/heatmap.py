"""
Scorer × entity heatmap.

The team uses this view to spot patterns in low scores — which scorers
consistently flag the same entities, which entities get red across the
board, which scorers are outliers.

Layout
------
  X axis : entity (proposal / research note / strategy note) — the
           items being scored, latest-first.
  Y axis : scorer (sorted by mean grade ASCENDING — weakest scorers
           float to the top so the failures read first).
  Cell   : the latest grade that (scorer, entity) pair emitted, coloured
           on the canonical 1-BAD → 5-EXCELLENT ramp from `quality.py`.

Tooltip
-------
Each cell exposes the entity title, scorer id, grade, quality label,
and the score date — so the team can hover to spot a pattern without
ducking into the drill-down expanders.

Design notes
------------
- Limited to 30 most-recent entities by default so the X axis stays
  readable on a laptop. The picker lets the team widen if needed.
- Cells with no score (scorer didn't run on that entity) render as
  empty paper-tinted gaps rather than zero, so the eye reads "missing"
  rather than "bad".
- Rectangle stroke is 0.5px in RULE colour so the grid feels like a
  table, not a blob.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from src.quality import HEATMAP_DOMAIN, HEATMAP_RANGE


def render_heatmap(
    df: pd.DataFrame,
    *,
    title_hint: str = "entity",
    height: int = 720,
    row_px: int = 22,
) -> alt.Chart:
    """Build the Altair heatmap from long-form data.

    Expected columns on ``df``::
        scorer_label    str   — 'RN-001 · Decision Panel Layout'
        scorer_mean     float — used for Y-axis sort order
        entity_short    str   — truncated entity title (X axis label)
        entity_full     str   — full title (tooltip)
        grade           int   — 1..5
        grade_label     str   — BAD / FAIR / GOOD / …
        scored_on       str   — ISO date
        reasoning_short str   — truncated reasoning for hover (optional)
        weakness_short  str   — truncated key weakness for hover (optional)
    """
    if df.empty:
        return alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_text(
            text="No scored data in the selected window."
        )

    # Sort scorers by their mean grade ASC so the weakest float to top —
    # the team is hunting low scores, not celebrating high ones.
    scorer_order = (
        df.sort_values("scorer_mean", ascending=True)["scorer_label"]
        .drop_duplicates()
        .tolist()
    )
    entity_order = (
        df.sort_values("entity_recency", ascending=False)["entity_short"]
        .drop_duplicates()
        .tolist()
    )

    base = alt.Chart(df).encode(
        x=alt.X(
            "entity_short:N",
            sort=entity_order,
            title=None,
            axis=alt.Axis(
                labelAngle=-35,
                labelLimit=160,
                labelFontSize=10,
                labelColor="#6D6682",
            ),
        ),
        y=alt.Y(
            "scorer_label:N",
            sort=scorer_order,
            title=None,
            axis=alt.Axis(
                labelLimit=300,
                labelFontSize=10,
                labelColor="#1A1530",
                # Force every scorer label to render. Altair's default
                # 'parity' strategy hides every other label when they
                # would overlap — disastrous here because every row is
                # a different rubric the team needs to identify.
                labelOverlap=False,
                labelPadding=4,
            ),
        ),
    )

    heat = base.mark_rect(stroke="#E2D8CA", strokeWidth=0.5).encode(
        color=alt.Color(
            "grade:O",
            scale=alt.Scale(domain=HEATMAP_DOMAIN, range=HEATMAP_RANGE),
            legend=alt.Legend(
                title="Grade",
                orient="bottom",
                direction="horizontal",
                labelExpr=(
                    "datum.value == 1 ? '1 · BAD' :"
                    "datum.value == 2 ? '2 · POOR' :"
                    "datum.value == 3 ? '3 · FAIR' :"
                    "datum.value == 4 ? '4 · GOOD' :"
                    "datum.value == 5 ? '5 · EXCELLENT' : datum.value"
                ),
            ),
        ),
        tooltip=[
            alt.Tooltip("entity_full:N", title=title_hint.title()),
            alt.Tooltip("scorer_label:N", title="Scorer"),
            alt.Tooltip("grade:Q", title="Grade"),
            alt.Tooltip("grade_label:N", title="Quality"),
            alt.Tooltip("scored_on:N", title="Scored"),
            *(
                [alt.Tooltip("reasoning_short:N", title="Reasoning")]
                if "reasoning_short" in df.columns else []
            ),
            *(
                [alt.Tooltip("weakness_short:N", title="Key weakness")]
                if "weakness_short" in df.columns else []
            ),
        ],
    )

    return heat.properties(height=height).configure_view(
        stroke=None
    ).configure_axis(
        domain=False,
        ticks=False,
    )
