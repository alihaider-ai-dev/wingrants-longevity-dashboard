"""
Password gate for the dashboard.

Single shared password (Ahmed + Ali only — max 5 viewers). The password
lives in `st.secrets["app_password"]` so it's never in source.

Design notes:
  - Failed attempts get a constant-time compare to defeat timing oracles
    (not a serious threat at this scale, but cheap insurance).
  - On success we set `st.session_state.auth_ok = True` and call rerun
    so the rest of the app renders. The flag survives reloads within
    a single browser tab.
  - We `st.stop()` after asking for the password so the heavy data
    queries never run before authentication.
"""

from __future__ import annotations

import hmac
import os

import streamlit as st


def _expected_password() -> str:
    """Read the password from st.secrets first, then env, then fail loudly."""
    try:
        return st.secrets["app_password"]
    except (KeyError, FileNotFoundError):
        pwd = os.getenv("APP_PASSWORD", "")
        if not pwd:
            st.error(
                "Dashboard not configured — `app_password` missing from "
                "Streamlit secrets and `APP_PASSWORD` missing from env. "
                "See README.md / `.streamlit/secrets.toml.example`."
            )
            st.stop()
        return pwd


def gate() -> None:
    """Block the rest of the page until the user enters the password.

    Idempotent — once authenticated within a session, subsequent calls
    are a no-op so page tabs don't re-prompt.
    """
    if st.session_state.get("auth_ok"):
        return

    st.markdown(
        """
        <div style="max-width:380px;margin:80px auto 0;padding:28px 26px;
                    background:#FBF7F1;border:1px solid #E2D8CA;
                    border-radius:12px;">
        <h2 style="margin:0 0 6px;font-family:Fraunces,Georgia,serif;
                   color:#1A1530;">WinGrants longevity</h2>
        <p style="margin:0 0 20px;color:#6D6682;font-size:13px;">
        Internal score-tracking dashboard. Enter the team password to
        continue.</p>
        """,
        unsafe_allow_html=True,
    )
    pwd = st.text_input("Password", type="password", label_visibility="collapsed")
    st.markdown("</div>", unsafe_allow_html=True)

    if not pwd:
        st.stop()

    if hmac.compare_digest(pwd, _expected_password()):
        st.session_state.auth_ok = True
        st.rerun()
    else:
        st.error("Wrong password.")
        st.stop()
