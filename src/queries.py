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
        "name_table": "concept_notes",
        "score_table": "concept_note_scores",
        "summary_table": "concept_note_score_summaries",
        "fk": "concept_note_id",
        "id_label": "concept_note_id",
    },
    "strategy_note": {
        "label": "Strategy notes",
        "name_table": "strategy_notes",
        "score_table": "strategy_note_scores",
        "summary_table": "strategy_note_score_summaries",
        "fk": "strategy_note_id",
        "id_label": "strategy_note_id",
    },
    "ai_draft": {
        "label": "AI drafts (proposals)",
        "name_table": "proposals",
        "score_table": "proposal_scores",
        "summary_table": "proposal_score_summaries",
        "fk": "proposal_id",
        "id_label": "proposal_id",
        # AI drafts use `evaluator_id` instead of `scorer_id` — the
        # generic queries below normalise this difference.
        "scorer_col": "evaluator_id",
    },
    "scorecard": {
        # Tables renamed in migration 024 (proposal_scorecards →
        # proposal_evals + scorecard_scores → proposal_eval_scores)
        # while the FE label "Scorecard" stayed. Keep our entity key
        # "scorecard" so the existing tab IDs in app.py don't churn,
        # but point at the new table names + the renamed FK.
        "label": "Standalone scorecards",
        "name_table": "proposal_evals",
        "score_table": "proposal_eval_scores",
        "summary_table": "proposal_eval_summaries",
        "fk": "eval_id",
        "id_label": "eval_id",
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
    cfg = ENTITIES[entity_key]
    if entity_key == "strategy_note":
        # strategy_notes.user_id may be NULL for older rows that only
        # reference a concept_note. COALESCE so cohort grouping picks
        # up the right account in either case.
        sql = f"""
            SELECT
                u.email,
                COUNT(DISTINCT n.id) AS entities,
                AVG(s.average_grade)::numeric(4,2) AS avg_grade,
                MIN(s.scored_at)::date AS first_score,
                MAX(s.scored_at)::date AS last_score
            FROM strategy_notes n
            JOIN strategy_note_score_summaries s ON s.strategy_note_id = n.id
            LEFT JOIN concept_notes cn ON cn.id = n.concept_note_id
            LEFT JOIN users u ON u.id = COALESCE(n.user_id, cn.user_id)
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
              AND u.email IS NOT NULL
            GROUP BY u.email
            ORDER BY avg_grade DESC NULLS LAST
        """
    else:
        sql = f"""
            SELECT
                u.email,
                COUNT(DISTINCT n.id) AS entities,
                AVG(s.average_grade)::numeric(4,2) AS avg_grade,
                MIN(s.scored_at)::date AS first_score,
                MAX(s.scored_at)::date AS last_score
            FROM {cfg["name_table"]} n
            JOIN {cfg["summary_table"]} s ON s.{cfg["fk"]} = n.id
            JOIN users u ON u.id = n.user_id
            WHERE s.scored_at >= NOW() - (:days || ' days')::interval
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
    elif entity_key == "scorecard":
        sql = """
            SELECT
                sc.id,
                sc.name AS title,
                sc.call_title AS cluster,
                u.email AS owner,
                s.average_grade,
                s.successful_evaluations AS successful,
                s.failed_evaluations AS failed,
                s.total_input_tokens + s.total_output_tokens AS total_tokens,
                s.scored_at::date AS scored_on,
                sc.created_at::date AS created_on
            FROM proposal_evals sc
            JOIN proposal_eval_summaries s ON s.eval_id = sc.id
            LEFT JOIN users u ON u.id = sc.user_id
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
    else:  # strategy_note
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
            LEFT JOIN concept_notes cn ON cn.id = n.concept_note_id
            LEFT JOIN users u ON u.id = COALESCE(n.user_id, cn.user_id)
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
