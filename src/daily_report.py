import asyncio
import os
import json
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
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
    """Generic helper to open SSE and call an MCP tool, returning list of dicts or texts."""
    async with sse_client(url.rstrip('/')) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name=tool, arguments=args)
            content_items = unwrap_content(result.content)
            if len(content_items) == 1 and isinstance(content_items[0], list):
                return content_items[0]
            return content_items

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    today_date = date.today()
    today = today_date.isoformat()

    # 1. Fetch today's commits across main
    commits = await post_mcp(
        GITHUB_MCP_URL,
        'list_commits',
        {
            'owner': GH_OWNER, 'repo': GH_REPO,
            'since': f"{today}T00:00:00Z",
            'until': f"{today}T23:59:59Z"
        }
    )
    if not commits:
        print("ℹ️ No commits today – skipping.")
        return

    # 2. Determine base and head for overall diff
    if len(commits) > 1:
        base_sha = commits[-1]['sha']
        head_sha = commits[0]['sha']
    else:
        # Only one commit: compare with yesterday's latest
        yesterday = today_date - timedelta(days=1)
        prev = await post_mcp(
            GITHUB_MCP_URL,
            'list_commits',
            {
                'owner': GH_OWNER, 'repo': GH_REPO,
                'since': f"{yesterday.isoformat()}T00:00:00Z",
                'until': f"{yesterday.isoformat()}T23:59:59Z"
            }
        )
        base_sha = prev[0]['sha'] if prev else commits[0]['sha']
        head_sha = commits[0]['sha']

    # 3. Fetch overall diff via compare_commits
    overall = await post_mcp(
        GITHUB_MCP_URL,
        'compare_commits',
        {
            'owner': GH_OWNER, 'repo': GH_REPO,
            'base': base_sha, 'head': head_sha
        }
    )
    overall_diff = overall[0] if overall else ""

    # 4. Fetch feature-branch commits and diff
    branch = os.getenv('TARGET_BRANCH', 'feature-branch')
    branch_commits = await post_mcp(
        GITHUB_MCP_URL,
        'list_commits',
        {
            'owner': GH_OWNER, 'repo': GH_REPO,
            'sha': branch,
            'since': f"{today}T00:00:00Z"
        }
    )
    if branch_commits:
        b_base = branch_commits[-1]['sha']
        b_head = branch_commits[0]['sha']
        branch_res = await post_mcp(
            GITHUB_MCP_URL,
            'compare_commits',
            {'owner': GH_OWNER, 'repo': GH_REPO, 'base': b_base, 'head': b_head}
        )
        branch_diff = branch_res[0] if branch_res else ""
    else:
        branch_diff = ""

    # 5. Summarize with LLM including diffs and branch context
    prompt = (
        f"【日付: {today}】\n"
        "以下の変更内容をもとに、3行の日本語日報を作成してください。\n\n"
        "■ 全体差分（mainブランチ）:\n" + overall_diff + "\n\n"
        f"■ `{branch}` ブランチでの実装差分:\n" + branch_diff
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    summary = resp.choices[0].message.content.strip()

    # 6. Post to Slack
    body = f"【{today}】 3行日報\n{summary}"
    print(body)
    await post_mcp(
        SLACK_MCP_URL,
        'slack_post_message',
        {'channel_id': SLACK_CHANNEL_ID, 'text': body},
    )

    # 7. Create Notion page
    props = {
        'Date': {'date': {'start': today}},
        'Summary': {'rich_text': [{'text': {'content': summary}}]}
    }
    await post_mcp(
        NOTION_MCP_URL,
        'create-page',
        {'parent_type': 'database_id', 'parent_id': NOTION_DB_ID, 'properties': json.dumps(props)}
    )

    print("✅ Daily report with LLM summary sent!")

if __name__ == '__main__':
    asyncio.run(main())
