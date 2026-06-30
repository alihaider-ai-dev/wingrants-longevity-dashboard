"""
Proposal *division* lookup — distinct from `scorer_sections.py`.

`scorer_sections.py` maps a SCORER id (EX-001, IM-012, …) to one of the
four EC sections. This module maps a proposal **division** — the
`section_id` stored on `proposal_scores` (`excellence_1_1`, `impact_2_2`,
`_proposal_wide`, …) — to its human label, its parent EC section, and a
stable sort order.

The eight division ids are stable in the BE engine (they mirror the EC
proposal template), so the labels are kept as a static map with a
prettify fallback for any future division the snapshot predates.
"""
from __future__ import annotations

import re

# Parent EC sections, in display order. Compliance is the catch-all for
# the cross-cutting `_proposal_wide` bucket (compliance + gatekeeper +
# concept-fidelity scorers all land there).
SECTION_ORDER = ["Excellence", "Impact", "Implementation", "Compliance"]

# division id → short human label (the EC sub-section title).
_DIVISION_LABELS = {
    "excellence_1_1":     "1.1 · Objectives and Ambition",
    "excellence_1_2":     "1.2 · Methodology and Approach",
    "impact_2_1":         "2.1 · Project Results and Impacts",
    "impact_2_2":         "2.2 · Dissemination, Exploitation, Communication",
    "impact_2_3":         "2.3 · Summary (Canvas)",
    "implementation_3_1": "3.1 · Work Plan and Resources",
    "implementation_3_2": "3.2 · Consortium Capacity and Management",
    "_proposal_wide":     "Proposal-wide · cross-cutting compliance",
}

# division-id prefix → parent EC section.
_PREFIX_SECTION = {
    "excellence":     "Excellence",
    "impact":         "Impact",
    "implementation": "Implementation",
}


def division_section(section_id: str | None) -> str:
    """Parent EC section for a division id. Anything that isn't an
    excellence_/impact_/implementation_ division (incl. `_proposal_wide`)
    folds into Compliance — same catch-all as the scorer map."""
    if not section_id:
        return "Compliance"
    prefix = str(section_id).split("_", 1)[0].lower()
    return _PREFIX_SECTION.get(prefix, "Compliance")


def division_label(section_id: str | None) -> str:
    """Human label for a division id, falling back to a prettified form
    of the raw id for any division the static map predates."""
    if not section_id:
        return "—"
    label = _DIVISION_LABELS.get(section_id)
    if label:
        return label
    # Fallback: 'something_4_2' → 'Something 4.2'
    pretty = str(section_id).lstrip("_").replace("_", " ").strip()
    return pretty[:1].upper() + pretty[1:] if pretty else str(section_id)


def division_sort_key(section_id: str | None) -> tuple:
    """Sort divisions by parent-section order, then by the trailing
    numeric (1_1 before 1_2 before 2_1). `_proposal_wide` sorts last."""
    section = division_section(section_id)
    sec_idx = SECTION_ORDER.index(section) if section in SECTION_ORDER else len(SECTION_ORDER)
    nums = [int(n) for n in re.findall(r"\d+", str(section_id or ""))]
    # `_proposal_wide` has no digits → push it to the end of its section.
    if not nums:
        nums = [99, 99]
    return (sec_idx, *nums)
