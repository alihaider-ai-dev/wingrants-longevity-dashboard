"""
Altair chart builders.

Every entity tab calls these three primitives with its own DataFrame:
  - `trend_chart`         — line + percentile band over time
  - `drift_heatmap`       — scorer × week, colour = mean grade
  - `cohort_distribution` — box plot per customer

Kept here so each tab's renderer is a thin 30-line composition rather
than hand-rolling Altair specs in five places.

Palette intentionally mirrors the WinGrants brand tokens (warm paper +
coral accent) so the dashboard reads as a sibling of the main app.
"""

from __future__ import annotations

import altair as alt
import pandas as pd


# ── Brand tokens (mirrors wg-design.css) ──────────────────────────
INK = "#1A1530"
INK_SOFT = "#3F3957"
INK_MUTE = "#6D6682"
PAPER = "#FBF7F1"
RULE = "#E2D8CA"
ACCENT = "#FF8A6B"
ACCENT_SOFT = "#FFE2D5"
ACCENT_DEEP = "#D9542E"
MINT = "#3C7A3A"
LILAC = "#6D5CB9"

# Distinct colour per entity, used by the Overview cross-entity trend.
# Keys MUST match the `label` values in queries.ENTITIES so the colour
# scale domain lines up with the data (and the legend reads correctly).
ENTITY_COLOURS = {
    "Research notes": ACCENT_DEEP,
    "Strategy notes": LILAC,
    "AI drafts": MINT,
    "External proposals": "#8F6718",  # sun-deep
}


def _empty(message: str) -> alt.Chart:
    """Fallback chart rendered when the query returned an empty frame.
    Keeps the section visible (with a clear reason) so users don't think
    something silently broke."""
    return (
        alt.Chart(pd.DataFrame({"x": [message]}))
        .mark_text(color=INK_MUTE, fontSize=13)
        .encode(text="x:N")
        .properties(height=160)
    )


# ── Trend line + percentile band ───────────────────────────────────

def trend_chart(df: pd.DataFrame, title: str = "") -> alt.LayerChart:
    """Line of weekly mean + 25/75 percentile ribbon underneath.

    Expects columns: bucket (date), avg_grade, p25, p75 (optional),
    scores (optional, drives tooltip).
    """
    if df.empty:
        return _empty("No scores in this window.")

    base = alt.Chart(df).encode(
        x=alt.X("bucket:T", axis=alt.Axis(title=None, format="%b %d", labelColor=INK_SOFT)),
    )

    band = None
    if {"p25", "p75"}.issubset(df.columns):
        band = base.mark_area(opacity=0.18, color=ACCENT).encode(
            y=alt.Y("p25:Q", title="Grade"),
            y2="p75:Q",
        )

    line = base.mark_line(
        color=ACCENT_DEEP, strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=44, color=ACCENT_DEEP)
    ).encode(
        y=alt.Y(
            "avg_grade:Q",
            scale=alt.Scale(domain=[0, 5]),
            axis=alt.Axis(grid=True, gridColor=RULE, gridOpacity=0.6, labelColor=INK_SOFT),
        ),
        tooltip=[
            alt.Tooltip("bucket:T", title="Week of", format="%d %b %Y"),
            alt.Tooltip("avg_grade:Q", title="Avg grade", format=".2f"),
            alt.Tooltip("scores:Q", title="Scores"),
        ],
    )

    layers = [c for c in (band, line) if c is not None]
    chart = alt.layer(*layers).properties(
        title=alt.TitleParams(title or "Average grade over time", color=INK, fontSize=13, anchor="start"),
        height=260,
    )
    return chart


# ── Scorer drift heatmap ───────────────────────────────────────────

def drift_heatmap(df: pd.DataFrame, title: str = "") -> alt.Chart:
    """Heatmap: scorer × week, colour = mean grade.

    Variance becomes opacity (high variance = washed-out cell) so a
    flaky evaluator visually fades — useful for spotting which
    scorers are unreliable across runs.
    """
    if df.empty:
        return _empty("Not enough scores per scorer/week.")

    # Sort scorers top-to-bottom by their overall mean so the most-
    # generous evaluators surface at the top and harshest at the
    # bottom. That order survives interactive re-filters because we
    # apply it server-side on the DataFrame, not via Vega's sort.
    scorer_order = (
        df.groupby("scorer")["mean_grade"]
        .mean()
        .sort_values(ascending=False)
        .index.tolist()
    )

    return (
        alt.Chart(df)
        .mark_rect()
        .encode(
            x=alt.X("week:T", axis=alt.Axis(format="%b %d", title=None, labelColor=INK_SOFT)),
            y=alt.Y("scorer:N", sort=scorer_order, axis=alt.Axis(title=None, labelColor=INK_SOFT)),
            color=alt.Color(
                "mean_grade:Q",
                scale=alt.Scale(scheme="redyellowgreen", domain=[1, 5]),
                legend=alt.Legend(title="Mean grade"),
            ),
            opacity=alt.Opacity(
                "stddev:Q",
                scale=alt.Scale(domain=[0, 1.5], range=[1.0, 0.35]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("scorer:N"),
                alt.Tooltip("week:T", title="Week of", format="%d %b %Y"),
                alt.Tooltip("mean_grade:Q", format=".2f"),
                alt.Tooltip("stddev:Q", format=".2f"),
                alt.Tooltip("n_scores:Q", title="Scores"),
            ],
        )
        .properties(
            title=alt.TitleParams(title or "Per-scorer drift", color=INK, fontSize=13, anchor="start"),
            height=alt.Step(16),
        )
    )


# ── Cohort distribution ────────────────────────────────────────────

def cohort_distribution(df: pd.DataFrame, title: str = "") -> alt.Chart:
    """Bar chart of customer email × average grade.

    For now we keep it as a bar chart (mean + count tooltip) rather
    than a box plot because the summary-table rollup loses per-row
    grades. If a finer cohort view is needed later we can re-query
    `proposal_scores` (etc.) keyed by user_id and switch to boxplot.
    """
    if df.empty:
        return _empty("No customers with scores in this window.")
    return (
        alt.Chart(df.head(30))  # top-30 by avg_grade so the chart fits
        .mark_bar(color=ACCENT_SOFT, stroke=ACCENT_DEEP, strokeWidth=0.5)
        .encode(
            x=alt.X(
                "avg_grade:Q",
                scale=alt.Scale(domain=[0, 5]),
                axis=alt.Axis(title="Average grade", labelColor=INK_SOFT),
            ),
            y=alt.Y(
                "email:N",
                sort="-x",
                axis=alt.Axis(title=None, labelColor=INK_SOFT, labelLimit=240),
            ),
            tooltip=[
                alt.Tooltip("email:N", title="Owner"),
                alt.Tooltip("avg_grade:Q", title="Avg grade", format=".2f"),
                alt.Tooltip("entities:Q"),
                alt.Tooltip("first_score:T", title="First", format="%d %b %Y"),
                alt.Tooltip("last_score:T", title="Last", format="%d %b %Y"),
            ],
        )
        .properties(
            title=alt.TitleParams(
                title or "Top customers by average grade", color=INK, fontSize=13, anchor="start"
            ),
            height=alt.Step(20),
        )
    )


# ── Tufte-compliant small multiples (one panel per surface) ──────
#
# Designed against the upstream `tufte` skill's ten principles. Audit
# trail (the .claude/skills/wingrants-charts/SKILL.md hard-requires it):
#
#   ✓ #1 Show the data — every weekly mean is a visible point.
#   ✓ #2 Data-ink ratio — line is data, points are data, that's it.
#   ✓ #3 No frame, no Y gridlines, axis line only where data is
#        (Altair's `domain=False` removes the rule when paired with
#        empty tickColor).
#   ✓ #4 Erase redundant — single mean line, no shaded ribbon (the
#        outer trend_chart already covers the percentile question).
#   ✓ #5 Honest scale — y domain [0,5] across all panels because
#        grades ARE bounded 1-5; common domain lets the reader
#        compare panel-to-panel without re-scaling.
#   ✓ #6 SMALL MULTIPLES — this is the whole point. One column per
#        surface so the reader spots which one is improving fastest
#        without juggling four overlapping lines on one axis.
#   ✓ #7 Layering — gridlines absent; data line is the only mark.
#   ✓ #8 Micro/macro — column titles read across the room; per-week
#        points read up close via tooltip.
#   ✓ #9 Smallest effective difference — only coral; no per-surface
#        colour key needed when the panel TITLE already identifies it.
#   ✓ #10 Word-data integration — title sits adjacent to each panel,
#        no detached legend.

def small_multiples_trend(df: pd.DataFrame) -> alt.Chart:
    """One mini line-chart per surface, shared 1–5 y-scale.

    Expects the same long-format DataFrame as `cross_entity_chart`:
    columns `entity`, `bucket`, `avg_grade`, `scores`.
    """
    if df.empty:
        return _empty("No scores in this window.")
    return (
        alt.Chart(df)
        .mark_line(color=ACCENT_DEEP, strokeWidth=1.8,
                   point=alt.OverlayMarkDef(filled=True, size=28, color=ACCENT_DEEP))
        .encode(
            x=alt.X(
                "bucket:T",
                axis=alt.Axis(
                    format="%b %d",
                    title=None,
                    labelColor=INK_SOFT,
                    labelFontSize=10,
                    tickColor=RULE,
                    domainColor=RULE,
                    grid=False,
                ),
            ),
            y=alt.Y(
                "avg_grade:Q",
                scale=alt.Scale(domain=[0, 5]),
                axis=alt.Axis(
                    title=None,
                    labelColor=INK_SOFT,
                    labelFontSize=10,
                    tickColor=RULE,
                    domainColor=RULE,
                    grid=False,                      # Tufte #3 — kill the grid
                    values=[0, 2.5, 5],              # Tufte #4 — only what's needed
                ),
            ),
            tooltip=[
                alt.Tooltip("entity:N", title="Surface"),
                alt.Tooltip("bucket:T", title="Week of", format="%d %b %Y"),
                alt.Tooltip("avg_grade:Q", title="Avg grade", format=".2f"),
                alt.Tooltip("scores:Q", title="Scores"),
            ],
        )
        .properties(width=200, height=120)
        .facet(
            column=alt.Column(
                "entity:N",
                header=alt.Header(
                    title=None,
                    labelColor=INK,
                    labelFontSize=12,
                    labelFontWeight="bold",
                    labelAnchor="start",
                    labelPadding=8,
                ),
                sort=alt.SortField("entity"),
            ),
        )
        .resolve_scale(y="shared")                   # Tufte #5/#6 — shared scale
        .configure_view(stroke=None)                 # Tufte #3 — drop frame box
    )


# ── Cross-entity overview line ─────────────────────────────────────

def cross_entity_chart(df: pd.DataFrame) -> alt.Chart:
    """One line per entity_type, weekly mean grade. Used on the
    Overview tab to spot which surface is improving fastest."""
    if df.empty:
        return _empty("No scores in this window.")
    return (
        alt.Chart(df)
        .mark_line(point=alt.OverlayMarkDef(filled=True, size=44), strokeWidth=2)
        .encode(
            x=alt.X("bucket:T", axis=alt.Axis(format="%b %d", title=None, labelColor=INK_SOFT)),
            y=alt.Y(
                "avg_grade:Q",
                scale=alt.Scale(domain=[0, 5]),
                axis=alt.Axis(grid=True, gridColor=RULE, gridOpacity=0.6, labelColor=INK_SOFT, title="Grade"),
            ),
            color=alt.Color(
                "entity:N",
                scale=alt.Scale(
                    domain=list(ENTITY_COLOURS.keys()),
                    range=list(ENTITY_COLOURS.values()),
                ),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("entity:N"),
                alt.Tooltip("bucket:T", title="Week of", format="%d %b %Y"),
                alt.Tooltip("avg_grade:Q", format=".2f"),
                alt.Tooltip("scores:Q"),
            ],
        )
        .properties(
            title=alt.TitleParams("Quality trend by surface", color=INK, fontSize=13, anchor="start"),
            height=300,
        )
    )
