"""
AI chart generator.

Pipeline:
  1. User pastes a SELECT and (optionally) a short prose intent.
  2. `safe_run_query` validates the SQL is SELECT-only + caps result
     size, runs against the read-only DB connection, returns a DF.
  3. `build_chart_spec` packages the DF schema + a row sample + the
     user's intent + the `wingrants-charts` SKILL.md + the upstream
     Tufte principles into one Anthropic message and asks for a
     Vega-Lite JSON spec back.
  4. The caller (`app.py`) renders the spec via
     `st.vega_lite_chart(spec, data=df)`.

Why a Vega-Lite spec instead of Python code
-------------------------------------------
A Vega-Lite spec is pure declarative JSON — no `exec()` of model
output, no arbitrary code execution. The model can't reach the DB,
the filesystem, or the network from inside a Vega-Lite spec; the
worst case is a broken visualisation.

Skill loading
-------------
At module-import time we read:
  - `.claude/skills/wingrants-charts/SKILL.md` (project skill)
  - `~/.claude/skills/tufte/principles.md`
  - `~/.claude/skills/tufte/chart-selection.md`
  - `~/.claude/skills/tufte/checklist.md`
The contents become the model's system prompt. Cached so we don't
re-read on every chart request.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from functools import lru_cache
from typing import Tuple

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.db import get_engine


# ── Anthropic client (cached singleton) ──────────────────────────

@st.cache_resource(show_spinner=False)
def _anthropic_client():
    """Return an Anthropic client or None if the key is missing."""
    try:
        key = st.secrets["anthropic_api_key"]
    except (KeyError, FileNotFoundError):
        key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    # Lazy-import so the rest of the app still runs even if anthropic
    # isn't installed in older environments.
    import anthropic
    return anthropic.Anthropic(api_key=key)


# ── Skill loader ─────────────────────────────────────────────────

_PROJECT_SKILL = pathlib.Path(__file__).parent.parent / ".claude" / "skills" / "wingrants-charts" / "SKILL.md"
_TUFTE_DIR = pathlib.Path.home() / ".claude" / "skills" / "tufte"


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Concatenate every skill file Claude needs into one prompt."""
    blocks: list[str] = [
        "You are a chart-generation tool inside the WinGrants longevity "
        "dashboard. Your only output is a single Vega-Lite v5 JSON spec, "
        "wrapped in a ```json fenced block. No commentary. No alternative "
        "specs. The host renders your output with "
        "`st.vega_lite_chart(spec, data=df)` so the spec must reference "
        "the implicit data source via `data: {name: 'data'}` (or omit "
        "`data` entirely — Streamlit injects the DataFrame).",
        "",
        "### Project skill — wingrants-charts",
    ]
    if _PROJECT_SKILL.exists():
        blocks.append(_PROJECT_SKILL.read_text())

    for name in ("principles.md", "chart-selection.md", "checklist.md"):
        p = _TUFTE_DIR / name
        if p.exists():
            blocks.append(f"\n### Upstream Tufte — {name}\n")
            blocks.append(p.read_text())

    blocks.append(
        "\n### Output format (strict)\n"
        "Respond with a single ```json fenced block containing one "
        "Vega-Lite spec. The spec MUST:\n"
        "  - omit the `data` key (Streamlit injects the DataFrame),\n"
        "  - use the brand palette tokens from src/charts.py "
        "    (ACCENT_DEEP #D9542E for the data, INK #1A1530 for text,\n"
        "    INK_SOFT #3F3957 for labels, RULE #E2D8CA for any line\n"
        "    that survives the Tufte 'erase non-data-ink' pass),\n"
        "  - omit gridlines (`axis.grid: false`) by default,\n"
        "  - omit the frame box (`config.view.stroke: null`),\n"
        "  - format dates `%d %b %Y` in tooltips and `%b %d` on axes,\n"
        "  - if a `scorer_id` column is being plotted, the spec must use\n"
        "    that column directly and the host will pre-map ids to\n"
        "    human labels before injecting.\n"
        "Nothing outside the fenced block. No prose."
    )
    return "\n".join(blocks)


# ── SQL safety ───────────────────────────────────────────────────

# Match a leading statement to determine if it's a SELECT. We also
# block multi-statement payloads by rejecting any `;` that isn't at
# the very end of a single trimmed string.
_SELECT_START_RE = re.compile(r"^\s*(WITH\b|SELECT\b)", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|COPY|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """Raised when the SQL fails the read-only gate."""


def _validate_select_only(sql: str) -> str:
    """Return the trimmed SQL or raise if it isn't safe."""
    body = sql.strip().rstrip(";")
    if ";" in body:
        raise UnsafeQueryError("Only a single statement is allowed (semicolons inside the query are blocked).")
    if not _SELECT_START_RE.match(body):
        raise UnsafeQueryError("Only SELECT (or WITH …) queries are allowed.")
    if _FORBIDDEN_KEYWORDS.search(body):
        raise UnsafeQueryError("Query contains a forbidden write-keyword. SELECT-only.")
    return body


# Hard caps on what we'll run + send to Claude. Cheap protection
# against pasting a 50M-row query.
_MAX_ROWS = 5000
_MAX_SAMPLE_ROWS = 30
_QUERY_TIMEOUT_SECONDS = 30


def safe_run_query(sql: str) -> Tuple[pd.DataFrame, str]:
    """Validate + run the user's query, returning (df, trimmed_sql).

    Adds an implicit `LIMIT _MAX_ROWS` if the query doesn't already
    have one, so the dashboard never hangs on a huge table.
    """
    trimmed = _validate_select_only(sql)
    has_limit = re.search(r"\bLIMIT\s+\d+\b", trimmed, re.IGNORECASE)
    capped = trimmed if has_limit else f"{trimmed}\nLIMIT {_MAX_ROWS}"

    eng = get_engine()
    with eng.connect() as conn:
        # Per-statement timeout so an accidental cross-join can't run
        # forever. PostgreSQL parses ms.
        conn.execute(text(f"SET LOCAL statement_timeout = {_QUERY_TIMEOUT_SECONDS * 1000}"))
        df = pd.read_sql(text(capped), conn)
    return df, capped


# ── Chart spec generator ─────────────────────────────────────────


def _df_schema_summary(df: pd.DataFrame) -> str:
    """A compact `column · dtype · sample` block for Claude."""
    lines = []
    for col, dt in df.dtypes.items():
        sample = df[col].dropna().head(3).tolist()
        # Trim long strings so we don't blow the context window.
        sample = [
            (s[:60] + "…" if isinstance(s, str) and len(s) > 60 else s)
            for s in sample
        ]
        lines.append(f"- `{col}` · {dt} · sample: {sample}")
    return "\n".join(lines)


def build_chart_spec(df: pd.DataFrame, intent: str) -> dict:
    """Ask Claude for a Vega-Lite spec describing the right chart for
    this data + intent. Returns the parsed dict.

    Raises if the API isn't configured or the response can't be parsed.
    """
    client = _anthropic_client()
    if client is None:
        raise RuntimeError(
            "Anthropic API key missing — add `anthropic_api_key` to "
            "Streamlit secrets or `ANTHROPIC_API_KEY` to .env"
        )
    if df.empty:
        raise RuntimeError("Query returned no rows — there's nothing to chart.")

    sample_rows = df.head(_MAX_SAMPLE_ROWS).to_dict(orient="records")

    user_msg = (
        f"### What the team wants to see\n"
        f"{intent.strip() or 'Choose the best Tufte-compliant chart for this data.'}\n\n"
        f"### DataFrame schema ({len(df):,} rows total)\n"
        f"{_df_schema_summary(df)}\n\n"
        f"### Row sample (first {len(sample_rows)})\n"
        f"```json\n{json.dumps(sample_rows, indent=2, default=str)}\n```\n"
    )

    # Sonnet is overkill for chart specs and Haiku struggles with the
    # full skill context — Claude 3.7 Sonnet is the right middle.
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=_system_prompt(),
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = "".join(block.text for block in resp.content if hasattr(block, "text"))
    spec = _extract_json(raw)
    # Strip any `data` key the model added — Streamlit injects the DF.
    spec.pop("data", None)
    return spec


def _extract_json(text: str) -> dict:
    """Pull the first JSON block out of Claude's reply.

    Accepts either a ```json``` fenced block or a bare object — both
    happen in practice depending on the temperature.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        # Fallback: take from the first `{` to the matching last `}`.
        first = text.find("{")
        last = text.rfind("}")
        if first < 0 or last <= first:
            raise RuntimeError("Claude's response contained no JSON object.")
        candidate = text[first : last + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Couldn't parse Claude's JSON: {exc}\n\n--- raw ---\n{text[:800]}")
