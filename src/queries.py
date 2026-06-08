"""
SQL queries — one function per (entity, lens) pair.

Every score table in the WinGrants schema has the same shape:
    id, <entity_id>, <scorer_id>, grade, grade_label, reasoning,
    key_weakness, model, input_tokens, output_tokens,
    cache_created_tokens, cache_read_tokens, error, scored_at

The three lenses (trend / drift / cohort) the user picked all reduce
to the same handful of GROUP BY shapes, so each entity gets the same
three functions. We parameterise on the entity-specific table + FK
column instead of duplicating SQL — kept readable by keeping each
query as a literal string template (Postgres ignores indentation
inside the literal anyway).

Date filters are clamped to a 365-day default ceiling to keep the
sidebar's "last N days" slider from accidentally pulling everything
in a wide-open query when the row count grows.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.db import run_query


# ── Entity configuration ────────────────────────────────────────────
# `name_table` / `fk` describe each entity to the generic query
# builders below. Adding a future entity (e.g. EU ESR scores) is a
# one-line addition here, no per-entity query duplication.

ENTITIES = {
    "research_note": {
        "label": "Research notes",
        "singular": "research note",       # for picker prompts + headers
        "scorer_prefix": "RN",              # codes are RN-001..RN-050
        "name_table": "concept_notes",
        "score_table": "concept_note_scores",
        "summary_table": "concept_note_score_summaries",
        "fk": "concept_note_id",
        "id_label": "concept_note_id",
    },
    "strategy_note": {
        "label": "Strategy notes",
        "singular": "strategy note",
        "scorer_prefix": "SN",              # codes are SN-001..SN-050
        "name_table": "strategy_notes",
        "score_table": "strategy_note_scores",
        "summary_table": "strategy_note_score_summaries",
        "fk": "strategy_note_id",
        "id_label": "strategy_note_id",
    },
    "ai_draft": {
        "label": "AI drafts",
        "singular": "proposal",             # AI drafts ARE proposals — the user's term
        # 365 evaluators across 8 prefix families: CO, CV, EX, GK, IM, IP, PSC, PSG
        "scorer_prefix": "CO/CV/EX/GK/IM/IP/PSC/PSG",
        "name_table": "proposals",
        "score_table": "proposal_scores",
        "summary_table": "proposal_score_summaries",
        "fk": "proposal_id",
        "id_label": "proposal_id",
        # AI drafts use `evaluator_id` instead of `scorer_id` — the
        # generic queries below normalise this difference.
        "scorer_col": "evaluator_id",
    },
}


def _scorer_col(entity_key: str) -> str:
    return ENTITIES[entity_key].get("scorer_col", "scorer_id")


# ── Lens 1: trend over time ─────────────────────────────────────────

def trend_over_time(
    entity_key: str,
    days: int = 90,
    granularity: str = "week",
) -> pd.DataFrame:
    """Average grade per time-bucket, plus the 25/50/75 percentile
    band so charts can render a confidence ribbon around the line.

    `granularity` ∈ {"day", "week", "month"} — Postgres `date_trunc`
    handles all three with no extra branching.
    """
    cfg = ENTITIES[entity_key]
    sql = f"""
        SELECT
            date_trunc(:granularity, scored_at)::date AS bucket,
            AVG(grade)::numeric(4,2) AS avg_grade,
            percentile_cont(0.25) WITHIN GROUP (ORDER BY grade)::numeric(4,2) AS p25,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY grade)::numeric(4,2) AS p50,
            percentile_cont(0.75) WITHIN GROUP (ORDER BY grade)::numeric(4,2) AS p75,
            COUNT(*) AS scores,
            COUNT(DISTINCT {cfg["fk"]}) AS entities_scored
        FROM {cfg["score_table"]}
        WHERE scored_at >= NOW() - (:days || ' days')::interval
          AND grade IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """
    return run_query(sql, {"granularity": granularity, "days": days})


# ── Lens 2: per-scorer drift ────────────────────────────────────────

def scorer_drift(entity_key: str, days: int = 90) -> pd.DataFrame:
    """Mean grade + variance for every (scorer, week) cell.

    Cells with fewer than 3 scores are dropped — a single outlier in a
    quiet week would dominate the colour and the heatmap would read as
    noise. Three is the same floor the BE scorer sanity-check uses.
    """
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    sql = f"""
        SELECT
            {scorer_col} AS scorer,
            date_trunc('week', scored_at)::date AS week,
            AVG(grade)::numeric(4,2) AS mean_grade,
            COALESCE(STDDEV(grade), 0)::numeric(4,2) AS stddev,
            COUNT(*) AS n_scores
        FROM {cfg["score_table"]}
        WHERE scored_at >= NOW() - (:days || ' days')::interval
          AND grade IS NOT NULL
        GROUP BY 1, 2
        HAVING COUNT(*) >= 3
        ORDER BY scorer, week
    """
    return run_query(sql, {"days": days})


# ── Lens 3: per-customer cohort ─────────────────────────────────────

def customer_cohort(entity_key: str, days: int = 365) -> pd.DataFrame:
    """One row per customer with their volume + average summary grade.

    Joins through the entity's name table to find user_id, then to
    `users` for the email. Strategy notes have a different user_id
    chain (they reference a concept_note which references a user),
    so we branch on entity_key.
    """
    # All four entity name-tables (concept_notes, strategy_notes,
    # proposals, proposal_evals) carry their own user_id column — no
    # cross-table chasing needed. The earlier strategy_note special-
    # case assumed strategy_notes referenced concept_notes via a
    # concept_note_id FK; in practice strategy_notes just embeds the
    # concept-note source as text (`concept_note_text`, `…_filename`),
    # so the FK lookup ran against a column that doesn't exist.
    cfg = ENTITIES[entity_key]
    sql = f"""
        SELECT
            u.email,
            COUNT(DISTINCT n.id) AS entities,
            AVG(s.average_grade)::numeric(4,2) AS avg_grade,
            MIN(s.scored_at)::date AS first_score,
            MAX(s.scored_at)::date AS last_score
        FROM {cfg["name_table"]} n
        JOIN {cfg["summary_table"]} s ON s.{cfg["fk"]} = n.id
        LEFT JOIN users u ON u.id = n.user_id
        WHERE s.scored_at >= NOW() - (:days || ' days')::interval
          AND u.email IS NOT NULL
        GROUP BY u.email
        ORDER BY avg_grade DESC NULLS LAST
    """
    return run_query(sql, {"days": days})


# ── Lens 4: drill-down table (per-entity summary) ──────────────────

def entity_summary(entity_key: str, days: int = 365) -> pd.DataFrame:
    """One row per entity with its summary score + last-modified date.

    Powers the drill-down table at the bottom of each entity tab. We
    keep the columns small enough to fit a Streamlit dataframe widget
    without horizontal scroll on a 1080p laptop.
    """
    cfg = ENTITIES[entity_key]
    if entity_key == "ai_draft":
        # Proposals carry a human title in `name` plus a cluster join.
        sql = """
            SELECT
                p.id,
                p.name AS title,
                c.name AS cluster,
                u.email AS owner,
                s.average_grade,
                s.successful_evaluations AS successful,
                s.failed_evaluations AS failed,
                s.total_input_tokens + s.total_output_tokens AS total_tokens,
                s.scored_at::date AS scored_on,
                p.created_at::date AS created_on
            FROM proposals p
            JOIN proposal_score_summaries s ON s.proposal_id = p.id
            LEFT JOIN users u ON u.id = p.user_id
            LEFT JOIN clusters c ON c.id = p.cluster_id
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
            ORDER BY s.scored_at DESC
        """
    elif entity_key == "research_note":
        sql = """
            SELECT
                n.id,
                COALESCE(n.name, n.id) AS title,
                u.email AS owner,
                s.average_grade,
                s.successful_scores AS successful,
                s.failed_scores AS failed,
                s.total_input_tokens + s.total_output_tokens AS total_tokens,
                s.scored_at::date AS scored_on,
                n.created_at::date AS created_on
            FROM concept_notes n
            JOIN concept_note_score_summaries s ON s.concept_note_id = n.id
            LEFT JOIN users u ON u.id = n.user_id
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
            ORDER BY s.scored_at DESC
        """
    else:  # strategy_note — uses its own user_id (no concept_note FK)
        sql = """
            SELECT
                n.id,
                COALESCE(n.name, n.id) AS title,
                u.email AS owner,
                s.average_grade,
                s.successful_scores AS successful,
                s.failed_scores AS failed,
                s.total_input_tokens + s.total_output_tokens AS total_tokens,
                s.scored_at::date AS scored_on,
                n.created_at::date AS created_on
            FROM strategy_notes n
            JOIN strategy_note_score_summaries s ON s.strategy_note_id = n.id
            LEFT JOIN users u ON u.id = n.user_id
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
            ORDER BY s.scored_at DESC
        """
    return run_query(sql, {"days": days})


# ── Top-line metrics for Overview ──────────────────────────────────

def overview_metrics(days: int = 90) -> pd.DataFrame:
    """One row per entity_kind with count + avg grade + total cost.

    Cost is approximated from input/output tokens. The constants below
    are the same per-token prices the BE billing layer uses for the
    Anthropic Claude 4.6 default scorer; if the BE model changes,
    update here too.
    """
    # Pricing (USD per 1M tokens) — Claude Sonnet 4.6 input/output mix.
    in_cost_per_mtok = 3.00
    out_cost_per_mtok = 15.00

    pieces = []
    for kind, cfg in ENTITIES.items():
        pieces.append(
            f"""
            SELECT
                '{cfg["label"]}' AS entity,
                COUNT(*) AS scores,
                COUNT(DISTINCT {cfg["fk"]}) AS entities_scored,
                AVG(grade)::numeric(4,2) AS avg_grade,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens,
                (
                    SUM(input_tokens)::numeric * {in_cost_per_mtok / 1_000_000.0:.10f}
                    + SUM(output_tokens)::numeric * {out_cost_per_mtok / 1_000_000.0:.10f}
                )::numeric(10,2) AS approx_cost_usd
            FROM {cfg["score_table"]}
            WHERE scored_at >= NOW() - (:days || ' days')::interval
              AND grade IS NOT NULL
            """
        )
    sql = "UNION ALL".join(pieces)
    return run_query(sql, {"days": days})


# ── Proposal/entity-centric queries ────────────────────────────────
#
# These power the "By proposal" picker + breakdown. The team flips
# between entity types (research note / strategy note / AI draft),
# picks one specific entity, and sees every evaluator's grade +
# reasoning for it — same way the BE's scorer pipeline outputs
# results.


def entity_list_with_scores(entity_key: str, days: int = 365) -> pd.DataFrame:
    """One row per entity that has at least one score in the window,
    sorted newest-first. Powers the proposal picker."""
    cfg = ENTITIES[entity_key]
    name_table = cfg["name_table"]
    fk = cfg["fk"]
    sql = f"""
        SELECT
            n.id,
            COALESCE(NULLIF(n.name, ''), n.id) AS title,
            u.email AS owner,
            ROUND(AVG(s.grade)::numeric, 2) AS avg_grade,
            COUNT(s.id) AS scorer_count,
            MAX(s.scored_at)::date AS last_scored_on
        FROM {name_table} n
        JOIN {cfg["score_table"]} s ON s.{fk} = n.id
        LEFT JOIN users u ON u.id = n.user_id
        WHERE s.scored_at >= NOW() - (:days || ' days')::interval
          AND s.grade IS NOT NULL
        GROUP BY n.id, n.name, u.email
        ORDER BY last_scored_on DESC, n.id
    """
    return run_query(sql, {"days": days})


def entity_score_breakdown(entity_key: str, entity_id: str) -> pd.DataFrame:
    """Every evaluator's score for ONE specific entity, ordered by
    grade ascending so the weakest scores read first (the team wants
    to fix the problems, not celebrate the wins)."""
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    sql = f"""
        SELECT
            s.{scorer_col} AS scorer_id,
            s.grade,
            s.grade_label,
            s.reasoning,
            s.key_weakness,
            s.model,
            s.scored_at::date AS scored_on
        FROM {cfg["score_table"]} s
        WHERE s.{cfg["fk"]} = :entity_id
          AND s.grade IS NOT NULL
        ORDER BY s.grade ASC, s.{scorer_col}
    """
    return run_query(sql, {"entity_id": entity_id})


# ── Evaluator-centric queries ──────────────────────────────────────


def evaluator_list_with_scores(entity_key: str, days: int = 365) -> pd.DataFrame:
    """One row per evaluator that scored at least one entity in the
    window, with their mean grade across all entities. Powers the
    evaluator picker."""
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    sql = f"""
        SELECT
            {scorer_col} AS scorer_id,
            ROUND(AVG(grade)::numeric, 2) AS mean_grade,
            ROUND(COALESCE(STDDEV(grade), 0)::numeric, 2) AS stddev,
            MIN(grade) AS min_grade,
            MAX(grade) AS max_grade,
            COUNT(DISTINCT {cfg["fk"]}) AS entities_scored,
            COUNT(*) AS total_scores
        FROM {cfg["score_table"]}
        WHERE scored_at >= NOW() - (:days || ' days')::interval
          AND grade IS NOT NULL
        GROUP BY {scorer_col}
        ORDER BY {scorer_col}
    """
    return run_query(sql, {"days": days})


def evaluator_score_breakdown(
    entity_key: str,
    scorer_id: str,
    days: int = 365,
) -> pd.DataFrame:
    """Every entity ONE specific evaluator scored, with grade +
    reasoning, sorted by grade ascending so the weakest entities
    surface first."""
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    name_table = cfg["name_table"]
    fk = cfg["fk"]
    sql = f"""
        SELECT
            n.id AS entity_id,
            COALESCE(NULLIF(n.name, ''), n.id) AS entity_title,
            u.email AS owner,
            s.grade,
            s.grade_label,
            s.reasoning,
            s.key_weakness,
            s.model,
            s.scored_at::date AS scored_on
        FROM {cfg["score_table"]} s
        JOIN {name_table} n ON n.id = s.{fk}
        LEFT JOIN users u ON u.id = n.user_id
        WHERE s.{scorer_col} = :scorer_id
          AND s.scored_at >= NOW() - (:days || ' days')::interval
          AND s.grade IS NOT NULL
        ORDER BY s.grade ASC, s.scored_at DESC
    """
    return run_query(sql, {"scorer_id": scorer_id, "days": days})


# ── Latest per-score detail (with reasoning + key_weakness) ────────

def latest_score_details(
    entity_key: str,
    days: int = 90,
    limit: int = 200,
) -> pd.DataFrame:
    """Return one row per individual score (newest first) with the
    LLM's reasoning + key_weakness so the team can read what every
    scorer actually said about each entity.

    Mirrors the schema across all 3 score tables — same columns, just
    different FK + scorer column name. The drill-down table at the
    bottom of each entity tab uses this to surface justifications.
    """
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    name_join_id = (
        "p.id" if entity_key == "ai_draft"
        else "n.id"
    )
    title_table = "proposals p" if entity_key == "ai_draft" else f'{cfg["name_table"]} n'
    title_alias = "p" if entity_key == "ai_draft" else "n"

    sql = f"""
        SELECT
            s.scored_at::date AS scored_on,
            COALESCE({title_alias}.name, {title_alias}.id) AS entity_title,
            s.{scorer_col} AS scorer_id,
            s.grade,
            s.grade_label,
            s.reasoning,
            s.key_weakness,
            s.model
        FROM {cfg["score_table"]} s
        JOIN {title_table} ON {name_join_id} = s.{cfg["fk"]}
        WHERE s.scored_at >= NOW() - (:days || ' days')::interval
          AND s.grade IS NOT NULL
        ORDER BY s.scored_at DESC, s.id
        LIMIT :limit
    """
    return run_query(sql, {"days": days, "limit": limit})


# ── Scorer × entity heatmap data ──────────────────────────────────


def heatmap_grid(
    entity_key: str,
    days: int = 90,
    entity_limit: int = 30,
    weak_only: bool = False,
) -> pd.DataFrame:
    """Long-form (scorer × entity) grid for the heatmap view.

    Returns one row per (scorer, entity) pair with the LATEST grade in
    the window — older grades on the same pair are dropped via a
    DISTINCT ON so the heatmap stays one cell per pair.

    Args:
        entity_key: 'research_note' / 'strategy_note' / 'ai_draft'.
        days: lookback window for scores.
        entity_limit: cap the X axis at the N most-recent entities so
            the heatmap stays readable. Default 30 fits a 1440-wide
            laptop without scrolling.
        weak_only: if True, restrict the result to (scorer, entity)
            pairs that scored ≤ 3 — the team's "fix the failures first"
            default.

    Returns columns:
        scorer_id, scorer_mean, entity_id, entity_short, entity_full,
        entity_recency, grade, grade_label, scored_on.
    """
    cfg = ENTITIES[entity_key]
    scorer_col = _scorer_col(entity_key)
    name_table = cfg["name_table"]
    fk = cfg["fk"]

    # Window of recent entities (latest score date wins) — caps X axis.
    # ROW_NUMBER + LATERAL would be cleaner but we want vanilla psql.
    weak_filter = "AND s.grade <= 3" if weak_only else ""

    sql = f"""
        WITH recent_entities AS (
            SELECT
                n.id AS entity_id,
                COALESCE(NULLIF(n.name, ''), n.id) AS entity_full,
                MAX(s.scored_at)::date AS entity_recency
            FROM {name_table} n
            JOIN {cfg["score_table"]} s ON s.{fk} = n.id
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
              AND s.grade IS NOT NULL
            GROUP BY n.id, n.name
            ORDER BY entity_recency DESC
            LIMIT :entity_limit
        ),
        latest_per_pair AS (
            SELECT DISTINCT ON (s.{scorer_col}, s.{fk})
                s.{scorer_col} AS scorer_id,
                s.{fk} AS entity_id,
                s.grade,
                s.grade_label,
                s.reasoning,
                s.key_weakness,
                s.scored_at::date AS scored_on
            FROM {cfg["score_table"]} s
            JOIN recent_entities r ON r.entity_id = s.{fk}
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
              AND s.grade IS NOT NULL
              {weak_filter}
            ORDER BY s.{scorer_col}, s.{fk}, s.scored_at DESC
        ),
        scorer_means AS (
            SELECT
                {scorer_col} AS scorer_id,
                ROUND(AVG(grade)::numeric, 2) AS scorer_mean
            FROM {cfg["score_table"]}
            WHERE scored_at >= NOW() - (:days || ' days')::interval
              AND grade IS NOT NULL
            GROUP BY {scorer_col}
        )
        SELECT
            p.scorer_id,
            m.scorer_mean,
            p.entity_id,
            r.entity_full,
            r.entity_recency,
            p.grade,
            p.grade_label,
            p.scored_on
        FROM latest_per_pair p
        JOIN recent_entities r ON r.entity_id = p.entity_id
        LEFT JOIN scorer_means m ON m.scorer_id = p.scorer_id
        ORDER BY m.scorer_mean ASC NULLS LAST, p.scorer_id, r.entity_recency DESC
    """
    return run_query(
        sql,
        {"days": days, "entity_limit": entity_limit},
    )


# ── Cross-cutting trend (for the Overview chart) ───────────────────

def cross_entity_trend(days: int = 90, granularity: str = "week") -> pd.DataFrame:
    """Weekly avg grade per entity type, stacked into one long DF so
    Altair can draw one line per entity colour-coded by `entity`."""
    pieces = []
    for kind, cfg in ENTITIES.items():
        pieces.append(
            f"""
            SELECT
                '{cfg["label"]}' AS entity,
                date_trunc(:granularity, scored_at)::date AS bucket,
                AVG(grade)::numeric(4,2) AS avg_grade,
                COUNT(*) AS scores
            FROM {cfg["score_table"]}
            WHERE scored_at >= NOW() - (:days || ' days')::interval
              AND grade IS NOT NULL
            GROUP BY 2
            """
        )
    sql = "UNION ALL".join(pieces) + " ORDER BY entity, bucket"
    return run_query(sql, {"granularity": granularity, "days": days})
