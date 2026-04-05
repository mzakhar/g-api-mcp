"""
g-api-mcp — Google APIs MCP server entry point.

Starts a stdio MCP server exposing Gmail, Google Calendar, and Google Tasks
tools. All tools return JSON-serialised response envelopes; see envelope.py.

Usage
-----
Start server (after running auth_setup.py once):
    python -m g_api_mcp.server

Claude Code / Claude Desktop registration:
    See .mcp.json in the project root.
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

# stdout is the JSON-RPC wire — all logging must go to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,
)

log = logging.getLogger(__name__)

mcp = FastMCP(
    "g-api-mcp",
    instructions=(
        "Google APIs MCP server providing Gmail, Calendar, Tasks, and Contacts access.\n\n"
        "IMPORTANT — how to use these tools efficiently:\n"
        "• All responses are JSON envelopes with `success`, `data`, `pagination`, "
        "`context_hint`, and `error` fields.\n"
        "• `context_hint.estimated_tokens` tells you how large this response is. "
        "Read it before deciding to fetch more.\n"
        "• If `context_hint.warning` is non-null, reconsider whether you need more data.\n"
        "• List tools (gmail_list_messages, calendar_list_events, tasks_list_tasks) return "
        "thin summaries only — no body content. Use these first to get IDs.\n"
        "• Get tools (gmail_get_message, calendar_get_event, tasks_get_task) fetch full "
        "content for one specific ID. Only call these for items you actually need to read.\n"
        "• To page through results, check `pagination.has_more` and pass "
        "`pagination.next_cursor` as `page_cursor` in the next call.\n"
        "• Auth errors mean you need to run `python auth_setup.py` in the project directory."
    ),
)

# Import tool modules — side effect: registers @mcp.tool() decorators
# Import order does not matter; all modules share the same `mcp` instance.
from g_api_mcp import gmail as _gmail  # noqa: F401 E402
from g_api_mcp import calendar as _calendar  # noqa: F401 E402
from g_api_mcp import tasks as _tasks  # noqa: F401 E402
from g_api_mcp import contacts as _contacts  # noqa: F401 E402
from g_api_mcp import sync as _sync  # noqa: F401 E402  — registers tasks_sync_to_vault tool


def main() -> None:
    log.info("g-api-mcp server starting (stdio transport)")
    mcp.run()


if __name__ == "__main__":
    main()
