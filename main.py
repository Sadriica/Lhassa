import asyncio
import sys
from loguru import logger
from potts.config import Config
from potts.voice.pipeline import run


def setup_logging(level: str):
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


def main():
    config = Config()
    setup_logging(config.log_level)
    logger.info("Starting Potts...")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Potts shutting down.")


if __name__ == "__main__":
    main()
