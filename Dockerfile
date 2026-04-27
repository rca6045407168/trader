# Reference Dockerfile — for future cloud migration to Lightsail/Fly/etc.
# Currently NOT used in production. GitHub Actions runs the trader on cron without containers.
# Keep this as a starting point if you outgrow GitHub Actions (rate limits, retention).

FROM python:3.11-slim

WORKDIR /app

# Install minimal system deps for compiled packages (numpy, pandas, scipy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 trader
USER trader

COPY --chown=trader:trader requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt
ENV PATH=/home/trader/.local/bin:$PATH

COPY --chown=trader:trader src/ ./src/
COPY --chown=trader:trader scripts/ ./scripts/
COPY --chown=trader:trader pyproject.toml .

# Volumes mounted at runtime: /app/data (journal), /app/.env (secrets)
VOLUME ["/app/data"]

# Default entrypoint — invoke specific script via CMD or override
ENTRYPOINT ["python"]
CMD ["scripts/run_daily.py", "--force"]
