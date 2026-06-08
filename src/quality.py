"""
Quality-label + colour helpers.

The team reads scores in two layers:
  - The raw 1-5 grade
  - A coarse quality word (BAD / FAIR / GOOD / EXCELLENT) for at-a-glance scanning

Mapping is intentionally asymmetric on the bad side — 1 and 2 are both
"bad" because the team's improvement work cares about pulling them up
together, not separating them.

    1 → BAD       (deep red)
    2 → POOR      (warm coral)
    3 → FAIR      (warm sun)
    4 → GOOD      (mint)
    5 → EXCELLENT (deep mint)
"""

from __future__ import annotations

from typing import Tuple


# Diverging warm-paper-compatible palette. Reds skew toward coral (the
# brand accent) so the heatmap reads as a sibling to the rest of the
# dashboard rather than a default Altair red-green ramp.
_PALETTE = {
    1: {"label": "BAD",       "bg": "#E84F2A", "fg": "#FFFFFF"},
    2: {"label": "POOR",      "bg": "#F4A988", "fg": "#5C2412"},
    3: {"label": "FAIR",      "bg": "#F0CE7C", "fg": "#5C4318"},
    4: {"label": "GOOD",      "bg": "#A5D49E", "fg": "#1F4B1D"},
    5: {"label": "EXCELLENT", "bg": "#5BA254", "fg": "#FFFFFF"},
}


def quality_label(grade: int | float | None) -> str:
    """Coarse quality word for a 1–5 grade. Empty string for None."""
    if grade is None:
        return ""
    try:
        g = int(round(float(grade)))
    except (TypeError, ValueError):
        return ""
    if g <= 1:
        return _PALETTE[1]["label"]
    if g >= 5:
        return _PALETTE[5]["label"]
    return _PALETTE[g]["label"]


def quality_color(grade: int | float | None) -> Tuple[str, str]:
    """(background, foreground) hex pair for a 1–5 grade."""
    if grade is None:
        return ("#F4EEE7", "#6D6682")
    try:
        g = int(round(float(grade)))
    except (TypeError, ValueError):
        return ("#F4EEE7", "#6D6682")
    g = max(1, min(5, g))
    return (_PALETTE[g]["bg"], _PALETTE[g]["fg"])


def grade_with_label(grade: int | float | None, db_label: str | None = None) -> str:
    """Render '3 · FAIR' / '4 · GOOD' style chip text.

    Prefers the BE's own ``grade_label`` column when present (different
    scorers may emit slightly different words like "ACCEPTABLE" instead
    of the canonical "FAIR"); falls back to the canonical mapping above.
    """
    if grade is None:
        return "—"
    lbl = (db_label or "").strip().upper() or quality_label(grade)
    try:
        g = int(round(float(grade)))
    except (TypeError, ValueError):
        return lbl or "—"
    return f"{g} · {lbl}" if lbl else str(g)


# Altair-friendly ordered domain for the colour scale.
HEATMAP_DOMAIN = [1, 2, 3, 4, 5]
HEATMAP_RANGE = [_PALETTE[g]["bg"] for g in HEATMAP_DOMAIN]
