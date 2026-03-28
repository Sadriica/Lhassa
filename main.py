import asyncio
import sys
from loguru import logger
from potts.config import Config
from potts.voice.pipeline import run
from potts.memory.session import close_session


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

    context, messages = None, []

    try:
        context, messages = asyncio.run(run())
    except KeyboardInterrupt:
        print()  # newline after ^C
        logger.info("Potts shutting down.")
    except ConnectionError as e:
        logger.error(f"Error de conexión: {e}")
        logger.warning("Verifica tu conexión a internet y las API keys.")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")

    if messages:
        logger.info("Guardando sesión en memoria...")
        try:
            asyncio.run(close_session(config.groq_api_key, config.model, messages))
            logger.info("Sesión guardada.")
        except Exception as e:
            logger.warning(f"No se pudo guardar la sesión: {e}")


if __name__ == "__main__":
    main()
