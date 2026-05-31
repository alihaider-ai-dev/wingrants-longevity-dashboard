"""
Database access layer for the dashboard.

Two ways the connection string reaches us:
  1. `st.secrets["db_url"]`     — production / Streamlit Cloud
  2. env var `DB_URL`           — local dev via .env

We hand the URL to SQLAlchemy because pandas' `read_sql` plays best
with a SQLAlchemy engine (psycopg2 raw cursors trigger pandas warnings
and lose some type coercion). The engine is cached as a Streamlit
resource so we don't spin up a fresh pool on every rerun.

Query results are cached at the call site via `@st.cache_data` with a
30-minute TTL — plenty fresh for a longevity study, and cheap enough
that adding `--cache-clear` to the sidebar covers the rare case where
someone wants to see a new score the moment it lands.

All queries here are SELECT-only and target the DB user
`wg_dashboard_ro` (see `.streamlit/secrets.toml.example` for the
`GRANT` statements that lock the role down to read-only).
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _db_url() -> str:
    try:
        url = st.secrets["db_url"]
    except (KeyError, FileNotFoundError):
        url = os.getenv("DB_URL", "")
    if not url:
        st.error(
            "DB connection string missing — set `db_url` in Streamlit "
            "secrets or `DB_URL` in your env. See "
            "`.streamlit/secrets.toml.example`."
        )
        st.stop()
    return url


@st.cache_resource(show_spinner=False)
def get_engine() -> Engine:
    """Singleton SQLAlchemy engine. Cached across reruns + sessions."""
    url = _db_url()
    # `pool_pre_ping=True` defeats stale connections after RDS failover
    # / maintenance windows — the dashboard sees those windows even
    # though it doesn't write, because RDS bounces idle connections.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )


@st.cache_data(ttl=1800, show_spinner=False)
def run_query(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    """Execute a SELECT and return a DataFrame.

    `params` are bound via SQLAlchemy's `text(...)` binding so all
    user-driven filter inputs are parameterised — the dashboard never
    interpolates user input into SQL.
    """
    eng = get_engine()
    with eng.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def healthcheck() -> bool:
    """Cheap connectivity probe used by the sidebar status pill."""
    try:
        df = run_query("SELECT 1 AS ok")
        return bool(len(df) == 1 and df.iloc[0]["ok"] == 1)
    except Exception:
        return False
