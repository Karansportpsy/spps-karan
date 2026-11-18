# SPPS Version 1.0 - Free: restricted-role FastAPI runtime.
# Staging retains its own lock. Production requires verified Supabase DB roles,
# TLS, FORCE RLS, migrations, owner email/password setup and explicit release approval.
# No staging-only data marker is required for production startup.
FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /opt/spps
COPY requirements-hosted.txt ./
RUN python -m pip install --no-cache-dir -r requirements-hosted.txt && \
    groupadd --system spps && useradd --system --gid spps --home /nonexistent spps
COPY server/ ./server/
COPY migrations/ ./migrations/
COPY web/ ./web/
RUN chown -R spps:spps /opt/spps
USER spps
EXPOSE 10000
# One worker: existing WebRTC signaling peers are stored in-process.
# Migration execution is opt-in and fails the deployment closed. Separate owner DB
# URLs are removed from the application process environment before Uvicorn starts.
CMD ["sh", "-ec", "if [ \"${SPPS_STARTUP_MIGRATIONS:-false}\" = \"true\" ]; then python -m server.release_migrations; fi; unset SPPS_CLINICAL_MIGRATION_URL SPPS_IDENTITY_MIGRATION_URL SPPS_SUPABASE_SERVICE_ROLE_KEY; exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --no-access-log --log-level info"]
