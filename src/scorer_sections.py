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
import re
from functools import lru_cache

# Display order for the four section heatmaps.
SECTION_ORDER = ["Excellence", "Impact", "Implementation", "Compliance"]

# Prefix → section fallback. The scorer-id families are stable in the BE
# scorecards.yaml, so a brand-new scorer (e.g. a future IP-038 that the
# JSON snapshot predates) still lands in the right section instead of
# silently falling to Compliance. Anything outside these families (RN/SN
# note scorers, ad-hoc ids) keeps the Compliance catch-all.
_PREFIX_SECTION = {
    "EX": "Excellence",
    "CV": "Impact",
    "IM": "Impact",
    "IP": "Implementation",
    "CF": "Compliance",
    "CO": "Compliance",
    "GK": "Compliance",
    "PSC": "Compliance",
    "PSG": "Compliance",
}


@lru_cache(maxsize=1)
def _section_map() -> dict[str, str]:
    path = pathlib.Path(__file__).parent / "scorer_sections.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def section_for(scorer_id: str | None) -> str:
    """Return the section for a scorer id.

    Resolution order:
      1. exact id in the extracted scorecards.yaml map,
      2. the scorer-id prefix family (EX/CV/IM/IP/CO/CF/GK/PSC/PSG),
      3. 'Compliance' catch-all so nothing disappears from the grid.
    """
    if not scorer_id:
        return "Compliance"
    exact = _section_map().get(scorer_id)
    if exact:
        return exact
    m = re.match(r"[A-Za-z]+", str(scorer_id))
    if m:
        return _PREFIX_SECTION.get(m.group(0).upper(), "Compliance")
    return "Compliance"
