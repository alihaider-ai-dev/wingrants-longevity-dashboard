"""
Consortium score extraction.

Unlike research/strategy/draft scores (which live in their own SQL
tables), consortium compliance scores sit inside the result JSON the
builder writes to S3. To get them onto the dashboard we have to:

  1. List consortium rows from Postgres (DB carries `results_s3_key`
     and the basic metadata).
  2. For each completed consortium, fetch the JSON blob from S3.
  3. Pull `compliance_audit.overall_score` (and the per-pillar
     sub-scores) out of the blob.
  4. Stitch the result into a long-format DataFrame the same
     chart/table primitives can consume.

We cache S3 fetches at the per-key granularity because a typical
result is ~1-2 MB and we don't want to re-fetch every blob on every
filter change.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

import boto3
import pandas as pd
import streamlit as st
from botocore.exceptions import ClientError

from src.db import run_query


# ── S3 client (cached resource) ─────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _s3_client():
    try:
        access_key = st.secrets["aws_access_key_id"]
        secret_key = st.secrets["aws_secret_access_key"]
        region = st.secrets["aws_region"]
    except (KeyError, FileNotFoundError):
        access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        region = os.getenv("AWS_REGION", "eu-central-1")
    if not access_key or not secret_key:
        return None
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def _bucket() -> str:
    try:
        return st.secrets["aws_s3_bucket"]
    except (KeyError, FileNotFoundError):
        return os.getenv("AWS_S3_BUCKET", "")


# ── Per-key fetch (1h cache) ────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_result(s3_key: str) -> dict | None:
    """Pull one consortium result blob. Returns None on any failure
    so a single bad row never blocks the whole dashboard."""
    s3 = _s3_client()
    bucket = _bucket()
    if not s3 or not bucket or not s3_key:
        return None
    try:
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        return json.loads(obj["Body"].read())
    except (ClientError, json.JSONDecodeError, ValueError):
        return None


def _audit_block(result: dict) -> dict | None:
    """Pull the compliance audit, tolerant of both top-level and
    nested layouts that the builder has used across versions."""
    if not isinstance(result, dict):
        return None
    direct = result.get("compliance_audit")
    if isinstance(direct, dict):
        return direct
    repair = result.get("compliance_repair") or {}
    return (repair.get("final_audit") if isinstance(repair, dict) else None)


# ── Public API ──────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def consortium_scores(days: int = 90) -> pd.DataFrame:
    """One row per Completed consortium with its overall score and
    per-pillar sub-scores. Empty DataFrame if S3 isn't configured."""
    s3 = _s3_client()
    if not s3:
        return pd.DataFrame()

    meta = run_query(
        """
        SELECT
            c.id,
            COALESCE(c.title, c.id) AS title,
            c.user_id,
            u.email AS owner,
            c.results_s3_key,
            c.created_at::date AS created_on,
            c.completed_at::date AS completed_on
        FROM consortiums c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.status = 'Completed'
          AND c.results_s3_key IS NOT NULL
          AND c.completed_at >= NOW() - (:days || ' days')::interval
        ORDER BY c.completed_at DESC
        """,
        {"days": days},
    )

    rows: list[dict] = []
    for _, m in meta.iterrows():
        result = _fetch_result(m["results_s3_key"])
        audit = _audit_block(result or {})
        if not audit:
            continue
        rows.append(
            {
                "id": m["id"],
                "title": m["title"],
                "owner": m["owner"],
                "completed_on": m["completed_on"],
                "overall_score": _safe_float(audit.get("overall_score")),
                "completeness": _safe_float(audit.get("completeness_score")),
                "balance": _safe_float(audit.get("balance_score")),
                "eligibility": _safe_float(audit.get("eligibility_score")),
                "issues_found": _len(audit.get("issues") or []),
                "model": audit.get("model"),
            }
        )
    return pd.DataFrame(rows)


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _len(seq: Iterable) -> int:
    try:
        return len(list(seq))
    except TypeError:
        return 0


def consortium_trend(days: int = 90, granularity: str = "week") -> pd.DataFrame:
    """Mean overall_score per time-bucket, mirroring `queries.trend_over_time`
    so the same Altair builder draws it."""
    df = consortium_scores(days=days)
    if df.empty:
        return pd.DataFrame(columns=["bucket", "avg_grade", "scores"])
    df = df.dropna(subset=["overall_score"]).copy()
    df["bucket"] = pd.to_datetime(df["completed_on"])
    rule = {"day": "D", "week": "W-MON", "month": "MS"}.get(granularity, "W-MON")
    grouped = (
        df.set_index("bucket")
        .groupby(pd.Grouper(freq=rule))
        .agg(avg_grade=("overall_score", "mean"), scores=("overall_score", "size"))
        .reset_index()
    )
    grouped["avg_grade"] = grouped["avg_grade"].round(2)
    grouped["bucket"] = grouped["bucket"].dt.date
    return grouped[grouped["scores"] > 0]
