"""
Scorer → proposal section lookup.

Each proposal scorer in the BE's scorecards.yaml carries a `category`
(excellence / impact / implementation / compliance / concept-fidelity /
gatekeeper). For the dashboard we fold those into the four EC evaluation
sections the team thinks in:

    Excellence · Impact · Implementation · Compliance (global)

where Compliance is the catch-all for the cross-cutting scorers
(compliance + concept-fidelity + gatekeeper).

The {scorer_id: section} map lives in `scorer_sections.json`, extracted
from the BE scorecards.yaml. Refresh it by re-running the extractor when
new proposal scorers are added.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

# Display order for the four section heatmaps.
SECTION_ORDER = ["Excellence", "Impact", "Implementation", "Compliance"]


@lru_cache(maxsize=1)
def _section_map() -> dict[str, str]:
    path = pathlib.Path(__file__).parent / "scorer_sections.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def section_for(scorer_id: str | None) -> str:
    """Return the section for a scorer id. Unknown ids (e.g. note scorers)
    fall to 'Compliance' so nothing silently disappears from the grid."""
    if not scorer_id:
        return "Compliance"
    return _section_map().get(scorer_id, "Compliance")
