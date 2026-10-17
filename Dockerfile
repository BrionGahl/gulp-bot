FROM python:3.13-slim

# No .pyc files written into the image at run time, and log lines straight to `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so that a change to the code alone doesn't reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY migrations ./migrations
COPY prompts ./prompts
COPY src ./src
ENV PYTHONPATH=/app/src

# The bot needs no privileges of its own.
RUN useradd --system --no-create-home gulp
USER gulp

EXPOSE 8080
CMD ["python", "-m", "gulp_bot"]
