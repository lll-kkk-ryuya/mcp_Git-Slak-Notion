from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from mcp import ClientSession
from mcp.client.sse import sse_client, HttpServerParameters

# ---------------------------------------------------------------------------
# Configuration (mcp.json)
# ---------------------------------------------------------------------------
# The script expects a file like:
# {
#   "mcpServers": {
#     "github": {
#       "url": "http://localhost:8001/sse",
#       "env": { "owner": "your-org", "repo": "your-repo" }
#     },
#     "slack":  {
#       "url": "http://localhost:8002/sse",
#       "env": { "SLACK_CHANNEL_ID": "C01234567" }
#     },
#     "notion": {
#       "url": "http://localhost:8003/sse",
#       "env": { "NOTION_DB_ID": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" }
#     }
#   }
# }
# ---------------------------------------------------------------------------

CONFIG_PATHS = [
    Path("mcp.json"),              # project root
    Path(".cursor/mcp.json"),      # Cursor IDE default
]

for p in CONFIG_PATHS:
    if p.exists():
        CONFIG_PATH = p
        break
else:
    raise SystemExit("✖️  mcp.json not found – create it first (see README)")

_CONFIG = json.loads(CONFIG_PATH.read_text())
SERVERS = _CONFIG["mcpServers"]

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def env(server: str, key: str):
    """Shorthand to grab an env var from a given server section."""

    return SERVERS[server]["env"][key]


def human_first_line(message: str) -> str:
    return message.split("\n")[0].strip()


async def connect(server: str):
    base = SERVERS[server]["url"].rstrip("/")
    params = HttpServerParameters(url=base)
    read, write = await sse_client(params)
    session = ClientSession(read, write)
    await session.initialize()
    return session


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------


async def main() -> None:
    today = date.today().isoformat()  # YYYY‑MM‑DD

    # –– GitHub commits ------------------------------------------------------
    github = await connect("github")
    commits = await github.call_tool(
        "list_commits",
        {
            "owner": env("github", "owner"),
            "repo": env("github", "repo"),
            "sha": "main",
            "since": f"{today}T00:00:00Z",
        },
    )

    messages = ["- " + human_first_line(c["commit"]["message"]) for c in commits]

    if not messages:
        print("ℹ️  No commits today – skipping notification.")
        return

    body = f"【{today}】\n" + "\n".join(messages)

    # –– Slack notification --------------------------------------------------
    slack = await connect("slack")
    await slack.call_tool(
        "slack_post_message",
        {
            "channel_id": env("slack", "SLACK_CHANNEL_ID"),
            "text": body,
        },
    )

    # –– Notion page ---------------------------------------------------------
    notion = await connect("notion")
    await notion.call_tool(
        "v1/pages",
        {
            "parent": {"database_id": env("notion", "NOTION_DB_ID")},
            "properties": {
                "Date": {"date": {"start": today}},
                "Summary": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "\n".join(messages),
                            }
                        }
                    ]
                },
            },
        },
    )

    print("✅ Daily report sent to Slack & Notion")


if __name__ == "__main__":
    asyncio.run(main())
