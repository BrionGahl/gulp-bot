"""A health check listener: answers any request with 200 OK.

Cloud Run requires the container to listen on `$PORT`. The bot itself is a long-lived gateway
connection with nothing to serve over HTTP, so this just satisfies container startup/liveness checks.
"""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"


async def _answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        # Drain (and discard) whatever request was sent; we don't route on it.
        await asyncio.wait_for(reader.read(1024), timeout=5)
        writer.write(RESPONSE)
        await writer.drain()
    except TimeoutError, OSError:
        pass
    finally:
        writer.close()


async def serve() -> None:
    port = os.environ.get("PORT", "8080")
    try:
        server = await asyncio.start_server(_answer, "0.0.0.0", int(port))
    except (OSError, ValueError) as e:
        log.error("Failed to bind health check listener on 0.0.0.0:%s: %s", port, e)
        return
    log.info("Health check listener bound to 0.0.0.0:%s", port)
    async with server:
        await server.serve_forever()
