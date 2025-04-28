# daily_report.py
"""Generate a simple one-shot "1 日 3 行" 日報

Uses OpenAI for LLM summarization, and MCP servers via SSE.
Configuration (endpoints & IDs) comes entirely from .env.
"""

from __future__ import annotations

import asyncio
import os
import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
import openai
from mcp import ClientSession
from mcp.client.sse import sse_client
import mcp.types as types

# ---------------------------------------------------------------------------
# Load environment from .env
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=Path('.env'), override=True)

# ---------------------------------------------------------------------------
# Environment variables (from .env)
# ---------------------------------------------------------------------------
GITHUB_MCP_URL   = os.getenv('GITHUB_MCP_URL')   or ''
SLACK_MCP_URL    = os.getenv('SLACK_MCP_URL')    or ''
NOTION_MCP_URL   = os.getenv('NOTION_MCP_URL')   or ''
GH_OWNER         = os.getenv('GH_OWNER')         or ''
GH_REPO          = os.getenv('GH_REPO')          or ''
SLACK_CHANNEL_ID = os.getenv('SLACK_CHANNEL_ID') or ''
NOTION_DB_ID     = os.getenv('NOTION_DB_ID')     or ''

# Initialize OpenAI client using new v1 API
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') or ''
if not OPENAI_API_KEY:
    raise SystemExit("✖️ Missing required env var: OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

# Validate required vars
required = {
    'GITHUB_MCP_URL': GITHUB_MCP_URL,
    'SLACK_MCP_URL': SLACK_MCP_URL,
    'NOTION_MCP_URL': NOTION_MCP_URL,
    'GH_OWNER': GH_OWNER,
    'GH_REPO': GH_REPO,
    'SLACK_CHANNEL_ID': SLACK_CHANNEL_ID,
    'NOTION_DB_ID': NOTION_DB_ID,
}
missing = [k for k, v in required.items() if not v]
if missing:
    raise SystemExit(f"✖️ Missing required env vars: {', '.join(missing)}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def unwrap_content(raw: list[types.Content]) -> list[dict]:
    """Convert MCP Content items into native Python dicts or lists."""
    data_items: list = []
    for item in raw:
        if isinstance(item, types.TextContent):
            text = item.text
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            data_items.append(parsed)
        elif isinstance(item, types.EmbeddedResource):
            data_items.append(item.body)
        else:
            data_items.append(item.__dict__)
    return data_items

async def post_mcp(url: str, tool: str, args: dict) -> list[dict]:
    """Generic helper to open SSE and call an MCP tool, returning list of dicts."""
    async with sse_client(url.rstrip('/')) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name=tool, arguments=args)
            content_items = unwrap_content(result.content)
            # If embedded JSON list, unwrap it
            if len(content_items) == 1 and isinstance(content_items[0], list):
                return content_items[0]
            return content_items

# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------
def summarize_with_llm(commits: list[dict]) -> str:
    """Take commit messages and return a 3-line Japanese summary via the new OpenAI Python client."""
    lines = [c.get('commit', {}).get('message', '').split('\n')[0] for c in commits]
    prompt = (
        "あなたは優秀な開発者向けの日報生成AIです。"
        "以下のコミットメッセージを3行の日本語で要約してください。\n\n" +
        "\n".join('- ' + l for l in lines)
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    today = date.today().isoformat()

    # 1. Fetch commits
    commits = await post_mcp(
        GITHUB_MCP_URL,
        'list_commits',
        {'owner': GH_OWNER, 'repo': GH_REPO, 'sha': 'main', 'since': f"{today}T00:00:00Z"}
    )
    if not commits:
        print("ℹ️ No commits – skipping.")
        return

    # 2. Summarize with LLM
    summary = summarize_with_llm(commits)
    body = f"【{today}】 3行日報\n{summary}"
    print(body)
    
    # 3. Post to Slack
    slack_result = await post_mcp(
        SLACK_MCP_URL,
        'slack_post_message',
        {'channel_id': SLACK_CHANNEL_ID, 'text': body},
    )
    
    # 4. Create Notion page using proper create-page args
    props = {
        'Date': {'date': {'start': today}},
        'Summary': {'rich_text': [{'text': {'content': summary}}]}
    }
    notion_result = await post_mcp(
        NOTION_MCP_URL,
        'create-page',
        {
            'parent_type': 'database_id',
            'parent_id': NOTION_DB_ID,
            'properties': json.dumps(props)
        }
    )
    print("Notion post result:", notion_result)

    print("✅ Daily report with LLM summary sent!")

if __name__ == '__main__':
    asyncio.run(main())
