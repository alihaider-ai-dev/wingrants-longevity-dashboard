"""
Scorer-name lookup.

The BE assigns each scorer/evaluator a short code (RN-001, CF-001,
CO-001, EX-007, …) that's stored in the score tables, but the
human-readable name only lives in the prompt-text blocks inside the
BE's `utils/scorers.py`, `utils/research_note_scorers.py`, and
`utils/strategy_note_scorers.py`. The dashboard can't import those
files directly (they're 14 000+ lines of LLM prompts each), so we
extract just the {id: name} mapping into `scorer_names.json`
at build time and look it up here.

Refreshing the JSON
-------------------
Run the snippet at the bottom of this file from the BE repo root —
it imports the registries once and rewrites the JSON. Add new
scorers to the BE first, then re-extract.
"""

from __future__ import annotations

import json
import pathlib
from functools import lru_cache


@lru_cache(maxsize=1)
def _name_map() -> dict[str, str]:
    """Load the ID → human name map from JSON (cached after first call)."""
    path = pathlib.Path(__file__).parent / "scorer_names.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def label_for(scorer_id: str | None) -> str:
    """Format the scorer id for display: 'RN-001 · Decision Panel Layout'.

    Falls back to the id alone when no human name is registered.
    """
    if not scorer_id:
        return "—"
    name = _name_map().get(scorer_id, "")
    if not name:
        return scorer_id
    return f"{scorer_id} · {name}"


# ── Refresh recipe (run from BE repo root) ───────────────────────────
#
#   import json, sys
#   sys.path.insert(0, '.')
#   from utils.scorers import EVALUATOR_NAMES
#   from utils.research_note_scorers import (
#       RESEARCH_NOTE_SCORERS, get_research_note_scorer_name,
#   )
#   from utils.strategy_note_scorers import (
#       STRATEGY_NOTE_SCORERS, get_strategy_note_scorer_name,
#   )
#
#   out = {**EVALUATOR_NAMES}
#   out.update({s: get_research_note_scorer_name(s) for s in RESEARCH_NOTE_SCORERS})
#   out.update({s: get_strategy_note_scorer_name(s) for s in STRATEGY_NOTE_SCORERS})
#
#   with open('../wingrants-longevity-dashboard/src/scorer_names.json', 'w') as f:
#       json.dump(out, f, indent=2, sort_keys=True)
