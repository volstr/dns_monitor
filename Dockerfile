# Один образ для web, checker и notifier. Зависимости — uv.lock, пересоздаётся так:
#   docker run --rm --user "$(id -u):$(id -g)" -e UV_CACHE_DIR=/tmp/uv -v "$PWD:/app" -w /app \
#     ghcr.io/astral-sh/uv:0.8.0-python3.12-bookworm-slim uv lock

FROM ghcr.io/astral-sh/uv:0.8.0 AS uv

FROM python:3.12-slim-bookworm

# UID и GID пользователя приложения. По умолчанию 1000; на хосте с другим UID передаются
# из .env, чтобы созданные в контейнере файлы правились с хоста без sudo (Q-A4).
ARG APP_UID=1000
ARG APP_GID=1000
# dev — с pytest, mypy и ruff; prod — только рабочие зависимости.
ARG DEPS=prod

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/app/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PATH=/opt/app/venv/bin:$PATH

COPY --from=uv /uv /usr/local/bin/uv

# Всё, куда приложение пишет, создаётся и отдаётся пользователю на этапе сборки.
# Пользователю отдаётся /opt/app, а не сам каталог окружения: uv пересоздаёт /opt/app/venv,
# а удаление каталога требует права записи в родительский. С владельцем-root у /opt
# сборка падает на `failed to remove directory: Permission denied`.
RUN groupadd -g "${APP_GID}" app \
 && useradd -u "${APP_UID}" -g "${APP_GID}" -m app \
 && install -d -o app -g app /app /opt/app

USER app
WORKDIR /app

COPY --chown=app:app pyproject.toml uv.lock ./
RUN if [ "${DEPS}" = "dev" ]; then uv sync --frozen; else uv sync --frozen --no-dev; fi

COPY --chown=app:app . .

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--access-logfile", "-"]
