FROM docker.io/library/python:3.12.15-slim AS base
RUN pip install --no-cache-dir uv==0.13.4
WORKDIR /app
COPY . .
RUN uv sync --frozen --all-packages --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home platform
USER platform
CMD ["uvicorn", "control_plane.entrypoints.api:app", "--host", "0.0.0.0", "--port", "8100", "--ws-max-size", "2097152"]

FROM base AS test
USER root
RUN uv sync --frozen --all-packages
USER platform
CMD ["pytest", "-q", "tests/contracts", "-p", "no:cacheprovider"]
