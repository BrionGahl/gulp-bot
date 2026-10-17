"""Starts the bot: reads the configuration, prepares the database, and keeps the Discord connection up
until the process is told to stop."""

from __future__ import annotations

import asyncio
import logging
import signal

from .bot import GulpBot
from .core.config import Config, ConfigError
from .core.database import connect
from .core.logging_setup import setup_logging
from .core.migrations import run_migrations
from .repository.guild_settings_repository import GuildSettingsRepository
from .service.guild_settings_service import GuildSettingsService

log = logging.getLogger("gulp_bot")


def main() -> None:
    try:
        config = Config.from_env()
    except ConfigError as e:
        raise SystemExit(str(e)) from None
    setup_logging(config.log_level)
    asyncio.run(run(config))


async def run(config: Config) -> None:
    try:
        pool = await connect(config.database_url)
    except Exception as e:
        raise SystemExit(f"Failed to connect to the database at `DATABASE_URL`: {e}") from None
    try:
        await run_migrations(pool)
    except Exception as e:
        raise SystemExit(f"Failed to run database migrations: {e}") from None

    guild_settings = await GuildSettingsService.load(GuildSettingsRepository(pool))
    bot = GulpBot(config, pool, guild_settings)
    try:
        await _run_until_stopped(bot, config.discord_token)
    finally:
        await pool.close()


async def _run_until_stopped(bot: GulpBot, token: str) -> None:
    # The bot is PID 1 in its container, and the kernel only delivers a signal to PID 1 if the process
    # handles it: without this, `docker stop` waits out its timeout and then kills the bot instead of letting
    # it close its Discord connection.
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):
            # No signal handlers outside Unix; Ctrl-C still stops the bot there.
            pass

    async with bot:
        runner = asyncio.create_task(bot.start(token))
        stopper = asyncio.create_task(stopping.wait())
        done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            log.info("Shutdown signal received, closing the Discord connection.")
            await bot.close()
        stopper.cancel()
        await runner
