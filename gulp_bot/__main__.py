"""Starts the bot: `python -m gulp_bot`."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import UTC, datetime

from . import db
from .config import Config, ConfigError
from .guild_settings import GuildSettingsStore

log = logging.getLogger("gulp_bot")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the fields Google Cloud Logging reads (`severity`, `time`), as the
    Rust bot's logs were."""

    SEVERITY = {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL"}

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "severity": self.SEVERITY.get(record.levelname, record.levelname),
            "target": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: int) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


async def run(config: Config) -> None:
    from .bot import GulpBot

    try:
        pool = await db.connect(config.database_url)
    except Exception as e:
        raise SystemExit(f"Failed to connect to the database at `DATABASE_URL`: {e}") from None
    try:
        await db.migrate(pool)
    except Exception as e:
        raise SystemExit(f"Failed to run database migrations: {e}") from None
    guild_settings = await GuildSettingsStore.load(pool)

    bot = GulpBot(config, pool, guild_settings)

    # The bot is PID 1 in its container, and the kernel only delivers a signal to PID 1 if the process
    # handles it: without this, `docker stop` waits out its timeout and then kills the bot instead of
    # letting it close its Discord connection.
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            # No signal handlers outside Unix; Ctrl-C still stops the bot there.
            pass

    async with bot:
        runner = asyncio.create_task(bot.start(config.discord_token))
        stopper = asyncio.create_task(stopping.wait())
        done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            log.info("Shutdown signal received, closing the Discord connection.")
            await bot.close()
        stopper.cancel()
        await runner
    await pool.close()


def main() -> None:
    try:
        config = Config.from_env()
    except ConfigError as e:
        raise SystemExit(str(e)) from None
    setup_logging(config.log_level)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
