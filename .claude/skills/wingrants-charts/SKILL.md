---
name: wingrants-charts
description: Use when generating, modifying, or critiquing a chart in the WinGrants longevity dashboard. Imports the upstream `tufte` skill for visual-design rules and adds WinGrants-specific data conventions (score tables, scorer-id naming, brand palette) so charts always read in the warm-paper brand voice + obey the ten Tufte principles. Triggers on any request to add/redesign a chart, improve a section's data viz, or convert an existing matplotlib/plotly thing to Tufte-clean Altair.
---

# WinGrants charts — Tufte-compliant Altair builders

Wraps the upstream **`tufte`** skill (10 principles · chart selection ·
the kill list) with the WinGrants-specific knowledge a chart in this
dashboard needs to be useful at first glance.

## When to invoke

- Building a new chart in `src/charts.py` or directly in `app.py`
- Replacing a default Streamlit `st.bar_chart` / `st.line_chart` with a
  considered Altair spec
- Critiquing an existing chart against the Tufte ten + the WinGrants
  brand voice

## Hard requirements (in order)

1. **Read the upstream Tufte skill first** —
   `~/.claude/skills/tufte/SKILL.md`,
   `~/.claude/skills/tufte/principles.md`,
   `~/.claude/skills/tufte/chart-selection.md`,
   `~/.claude/skills/tufte/kill-list.md`,
   `~/.claude/skills/tufte/checklist.md`. Apply principles 1–10 verbatim.
2. **Brand palette only.** Use the tokens defined in `src/charts.py`:
   ```
   INK         #1A1530     primary text + line colour
   INK_SOFT    #3F3957     axis labels
   INK_MUTE    #6D6682     captions, tooltips
   PAPER       #FBF7F1     surface (matches Streamlit theme)
   RULE        #E2D8CA     gridlines (use sparingly)
   ACCENT      #FF8A6B     coral — soft accent for fills
   ACCENT_DEEP #D9542E     coral-deep — primary data colour
   MINT/LILAC  #3C7A3A/#6D5CB9   secondary entity colours only
   ```
   Default to **`ACCENT_DEEP` for the data line/bar/point** and
   `INK_MUTE` for everything else. Tufte principle #9: "make all
   visual distinctions as subtle as possible, but still clear and
   effective". Two colours per chart maximum unless small-multiples
   demand more.
3. **Erase gridlines + frame.** Override Altair's default by setting
   `axis=alt.Axis(grid=False, domainColor=RULE, tickColor=RULE,
   labelColor=INK_SOFT, titleColor=INK)` on every encoding. The
   warm-paper background already provides enough containment; a frame
   box adds noise.
4. **Tooltip the data, not the chrome.** Every data point gets a
   tooltip with the human-readable scorer label
   (`scorer_names.label_for(scorer_id)` — never raw `RN-001`),
   the week formatted `%d %b %Y`, and the metric to 2 decimals.
5. **Empty-state grace.** If the underlying DataFrame is empty,
   return `charts._empty("Reason …")` — never raise. The reason
   string should tell the user what filter to widen.

## Score-data conventions

Quick reference for every chart in this dashboard:

| Column | Type | Notes |
|---|---|---|
| `grade` | integer 1–5 | Bar charts of grade MUST start at 0 (principle #5). |
| `grade_label` | text | EXCELLENT/GOOD/ACCEPTABLE/POOR/FATAL. Use for the colour scale only if encoding qualitative grade tiers. |
| `scorer_id` | text | Short code (RN-001, CO-007, etc). Always pass through `scorer_names.label_for()` before rendering. |
| `scored_at` | timestamp | Use `date_trunc('week', …)` in SQL so trends bucket consistently. |
| `model` | text | Show in the tooltip, not on the axis. |
| `reasoning`, `key_weakness` | text | NEVER plot — these are detail-card content only. |

## Default chart selection (per Tufte's chart-selection.md)

| Question the team is answering | Pick |
|---|---|
| "Is quality drifting week over week?" | Time-series line, faint percentile ribbon, no markers below 6 rows |
| "Which scorer is harshest / most generous?" | Strip plot of grade by scorer (sorted by mean) or **small multiples** if comparing across entity types |
| "How do customer cohorts compare?" | Horizontal bar, sorted by metric, single accent colour |
| "What's the grade distribution?" | Histogram with 5 bins (one per grade), no kernel density (it implies continuous) |
| "Per-scorer × per-week pattern?" | Sequential heatmap (one diverging colour scale, never rainbow) |

## Authoritative example

The `trend_chart` builder in `src/charts.py` is the gold standard
this skill enforces. Read it before writing a new builder.

When asked to write a new chart:

1. Start from the closest existing builder in `src/charts.py`.
2. Run through `~/.claude/skills/tufte/checklist.md` line by line —
   tick each item off in a code comment above the chart so reviewers
   can audit later.
3. Wire it into `src/charts.py` (not `app.py`) so future tabs reuse
   it.
4. Add a one-shot sample call into the new "🎨 Chart playground" tab
   in `app.py` (see test-fixture.py) so the team can eyeball the
   output without filling in dashboard filters.

## Cross-reference

- Upstream Tufte rules: `~/.claude/skills/tufte/SKILL.md`
- The 10 principles (verbatim): `~/.claude/skills/tufte/principles.md`
- Don't-do list: `~/.claude/skills/tufte/kill-list.md`
- Chart-type chooser: `~/.claude/skills/tufte/chart-selection.md`
- Pre-merge audit: `~/.claude/skills/tufte/checklist.md`
- WinGrants brand colours + existing builders: `src/charts.py`
- Scorer ID → human name: `src/scorer_names.py::label_for()`
