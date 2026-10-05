FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Default to API server
CMD ["uv", "run", "uvicorn", "src.openship.main:app", "--host", "0.0.0.0", "--port", "8000"]
