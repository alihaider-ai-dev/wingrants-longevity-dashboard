# WinGrant Scorers

Internal Streamlit dashboard that surfaces every score the WinGrants
platform records — research notes, strategy notes, AI-drafted
proposals, standalone scorecards, and consortium audits — so the team
can watch quality trend over weeks and catch evaluator drift early.

Five tabs, one filter sidebar. Each entity tab renders the same three
sections (trend over time · per-evaluator drift · per-customer cohort)
plus a drill-down table with CSV export.

## Quick start (local)

```bash
git clone https://github.com/<your-org>/wingrants-longevity-dashboard
cd wingrants-longevity-dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in DB_URL, APP_PASSWORD, AWS_*

streamlit run app.py
```

Visit `http://localhost:8501`, enter the password from `.env`, and
you're in.

## Deploy to Streamlit Cloud (the target host)

1. Push this repo to GitHub (private OK — Streamlit Cloud supports it
   for paid tiers; free tier requires public).
2. Go to <https://share.streamlit.io>, click **New app**, pick this
   repo + branch + `app.py`.
3. Click **Advanced settings → Secrets** and paste:
   ```toml
   app_password = "<your strong shared password>"
   db_url = "postgresql://wg_dashboard_ro:<pw>@<rds-host>:5432/wingrants?sslmode=require"
   aws_access_key_id = "<read-only IAM key>"
   aws_secret_access_key = "<read-only IAM secret>"
   aws_region = "eu-central-1"
   aws_s3_bucket = "wingrants-results"
   ```
4. Deploy. Streamlit gives you a `https://*.streamlit.app/` URL —
   share it with the team plus the password.

## DB connectivity

The dashboard needs a **read-only Postgres user** + **outbound access
from Streamlit Cloud's IP range to your RDS instance**.

Create the read-only role:

```sql
CREATE USER wg_dashboard_ro WITH PASSWORD '<long random>';
GRANT CONNECT ON DATABASE wingrants TO wg_dashboard_ro;
GRANT USAGE ON SCHEMA public TO wg_dashboard_ro;
GRANT SELECT ON
    concept_notes, concept_note_scores, concept_note_score_summaries,
    strategy_notes, strategy_note_scores, strategy_note_score_summaries,
    proposals, proposal_scores, proposal_score_summaries,
    proposal_scorecards, scorecard_scores, scorecard_summaries,
    consortiums, clusters, users
TO wg_dashboard_ro;

-- Defensive: revoke everything else, just in case future tables get
-- a default ACL that includes this role.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM wg_dashboard_ro;
GRANT SELECT ON
    concept_notes, concept_note_scores, concept_note_score_summaries,
    strategy_notes, strategy_note_scores, strategy_note_score_summaries,
    proposals, proposal_scores, proposal_score_summaries,
    proposal_scorecards, scorecard_scores, scorecard_summaries,
    consortiums, clusters, users
TO wg_dashboard_ro;
```

### Allowing Streamlit Cloud to reach RDS

Streamlit Cloud egress IPs are not stable. Two options:

1. **Public + SSL** (recommended for a low-stakes internal
   dashboard): mark the RDS instance "Publicly accessible: yes",
   keep `sslmode=require` in the connection string, set the security
   group to allow `0.0.0.0/0` on port 5432, and rely on the strong
   password + `wg_dashboard_ro` role + SELECT-only grants for safety.

2. **Egress IP pinning** (more secure): inspect a recent Streamlit
   request in CloudWatch logs to find the current egress IP, add it
   to the SG, and update when it rotates.

If neither option is acceptable, the alternative is to self-host the
dashboard inside the same VPC (the included `Dockerfile` is ready for
that — drop it onto a t4g.small in ECS or App Runner).

## S3 access (consortium scores)

The consortium overall_score lives in the result JSONB on S3, not in a
SQL table. Create a tiny IAM user with `s3:GetObject` on the
`wingrants-results` bucket and only those keys whose prefix matches
`consortiums/*`, then paste the access key into Streamlit secrets.

## Architecture

```
app.py                       Streamlit entry — page config, auth, tabs
.streamlit/config.toml       Brand-matched theme (warm paper + coral)
.streamlit/secrets.toml      <not committed> — app_password, db_url, AWS keys

src/
├── auth.py                  Password gate via st.secrets
├── db.py                    Cached SQLAlchemy engine + run_query
├── queries.py               One fn per (entity, lens) — trend / drift / cohort
├── consortium_extract.py    Pulls overall_score out of S3 JSONB
├── charts.py                Altair builders for the 4 chart types
└── filters.py               Sidebar lookback + granularity controls
```

Cache strategy:
- `@st.cache_resource` on the DB engine + S3 client (singleton).
- `@st.cache_data(ttl=1800)` on every query result.
- Sidebar **Refresh data** button clears the cache and reruns.

## Adding a new entity

Edit `src/queries.py` and add a row to `ENTITIES`:

```python
ENTITIES["eu_esr"] = {
    "label": "EU ESR scores",
    "name_table": "independent_evaluations",
    "score_table": "esr_scores",
    "summary_table": "esr_score_summaries",
    "fk": "evaluation_id",
    "scorer_col": "evaluator_id",  # if non-default
}
```

…then add a new tab in `app.py` that calls `_entity_tab("eu_esr", "EU ESR scores")`.
No other changes required — the trend / drift / cohort generic builders pick up the new entity automatically.

## Refresh cadence

Each query result is cached for 30 minutes. Click **Refresh data** in
the sidebar to bust the cache and pull fresh rows immediately.
