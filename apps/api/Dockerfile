FROM python:3.11-slim AS runtime
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.10 \
    && uv sync --frozen --no-dev
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
RUN groupadd --gid 1000 openship \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin openship \
    && mkdir -p /var/lib/openship/artifacts \
    && chown -R 1000:1000 /app /home/openship /var/lib/openship
ENV HOME=/home/openship

# Source-mounted Compose development needs the same locked runtime packages plus
# watchmedo and the rest of the repository's development dependency group.
FROM runtime AS development
RUN uv sync --frozen --all-groups
USER 1000:1000

FROM runtime AS production
USER 1000:1000
# Default to API server
CMD ["uv", "run", "uvicorn", "src.openship.main:app", "--host", "0.0.0.0", "--port", "8000"]
