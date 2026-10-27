FROM python:3.11-slim AS runtime
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.10 \
    && uv sync --frozen --no-dev
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Source-mounted Compose development needs the same locked runtime packages plus
# watchmedo and the rest of the repository's development dependency group.
FROM runtime AS development
RUN uv sync --frozen --all-groups

FROM runtime AS production
# Default to API server
CMD ["uv", "run", "uvicorn", "src.openship.main:app", "--host", "0.0.0.0", "--port", "8000"]
