import asyncio
import logging
import time

from app.config import settings
from app.services.worker import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("social_studio.worker")


async def main(platform: str | None = None):
    logger.info("Durable publishing worker started (platform=%s)", platform or "all")
    next_refresh = 0
    while True:
        try:
            if platform in (None, "instagram") and time.monotonic() >= next_refresh:
                from app.services.instagram_connections import refresh_due_connections
                await refresh_due_connections()
                next_refresh = time.monotonic() + 3600
            processed = await run_once(platform=platform)
        except Exception:
            logger.exception("Worker iteration failed")
            processed = False
        if not processed:
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
