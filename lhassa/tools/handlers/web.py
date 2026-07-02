"""Web / search tool handlers."""

from loguru import logger

from pipecat.services.llm_service import FunctionCallParams

from lhassa.tools.handlers._shared import _bump_tool


async def handle_web_search(params: FunctionCallParams):
    query = params.arguments.get("query", "")
    max_results = params.arguments.get("max_results", 4)
    logger.info(f"[tool] web_search: {query!r}")
    print(f"\033[2m[🔍 searching: {query}]\033[0m")
    _bump_tool()

    try:
        from lhassa.tools.search import web_search
        results = await web_search(query, max_results)
        if not results:
            await params.result_callback("No results found for that query.")
            return
        formatted = "\n\n".join(f"**{r['title']}**\n{r['body']}" for r in results)
        await params.result_callback(formatted)
    except Exception as e:
        logger.error(f"web_search error: {e}")
        await params.result_callback(f"Search error: {e}")
