"""Vision tool handlers (screen + image analysis)."""

from loguru import logger

from pipecat.services.llm_service import FunctionCallParams

from lhassa.tools.handlers._shared import _bump_tool


async def handle_analyze_screen(params: FunctionCallParams):
    question = params.arguments.get("question", "Describe what you see in detail.")
    logger.info(f"[tool] analyze_screen: {question!r}")
    print("\033[2m[👁 analyzing screen...]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.vision import take_screenshot, analyze_image
        screenshot = await take_screenshot()
        result = await analyze_image(screenshot, question)
        await params.result_callback(result)
    except Exception as e:
        logger.error(f"analyze_screen error: {e}")
        await params.result_callback(f"Vision error: {e}")


async def handle_analyze_image(params: FunctionCallParams):
    path = params.arguments.get("path", "")
    question = params.arguments.get("question", "Describe what you see in detail.")
    logger.info(f"[tool] analyze_image: {path!r}")
    print(f"\033[2m[👁 analyzing image: {path}]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.vision import analyze_image_file
        result = await analyze_image_file(path, question)
        await params.result_callback(result)
    except FileNotFoundError:
        await params.result_callback(f"Image file not found: {path}")
    except Exception as e:
        logger.error(f"analyze_image error: {e}")
        await params.result_callback(f"Vision error: {e}")
