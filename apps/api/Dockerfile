FROM python:3.14.7-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.12.10

COPY pyproject.toml uv.lock README.md ./
COPY apps/api/src ./apps/api/src

RUN uv sync --frozen --no-dev --no-editable


FROM python:3.14.7-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The application image is immutable at runtime. Remove pip/ensurepip and their
# vendored dependency tree from the final stage to reduce attack surface.
RUN rm -rf \
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.14/ensurepip \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app apps/api/migrations ./apps/api/migrations

USER app

EXPOSE 8000

CMD ["uvicorn", "customers_manager_hub.main:app", "--host", "0.0.0.0", "--port", "8000"]
