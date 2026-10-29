FROM python:3.14-slim

# uv installs the dependencies exactly as `uv.lock` pins them. Copied in from its own image; the version is
# pinned so that the image doesn't change by itself.
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

# No .pyc files written into the image at run time, and log lines straight to `docker logs`. uv uses the
# image's Python, never downloads one, and puts the environment in /app/.venv.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so that a change to the code alone doesn't reinstall them. `--frozen` installs what
# the lockfile says without updating it, `--no-dev` leaves out the test tools, and `--no-install-project`
# leaves out the bot itself, which runs from src/ below.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY migrations ./migrations
COPY ext_resources ./ext_resources
COPY src ./src
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src

# The bot needs no privileges of its own.
RUN useradd --system --no-create-home gulp
USER gulp

EXPOSE 8080
CMD ["python", "-m", "gulp_bot"]
