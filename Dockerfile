# Optional — for self-hosting (Streamlit Cloud doesn't use this).
# Builds a small image that runs `streamlit run app.py` on $PORT.

FROM python:3.13-slim

WORKDIR /app

# System deps for psycopg2 — libpq + a build chain for the wheels
# pyarrow may need at runtime. Slim image is missing both by default.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# Streamlit reads $PORT when --server.port is omitted (common on
# managed PaaS like Fly.io / Render); falls back to 8501 otherwise.
CMD streamlit run app.py \
    --server.port="${PORT:-8501}" \
    --server.headless=true \
    --server.address=0.0.0.0
