
import { readFileSync } from "fs";
import path from "path";
import { MCPSession } from "@modelcontextprotocol/sdk";


// Utility: resolve config file (project > global)
function loadMcpConfig() {
  const projectCfg = path.resolve(process.cwd(), ".cursor", "mcp.json");
  const homeCfg = path.resolve(process.env.HOME || "~", ".cursor", "mcp.json");
  const file = [projectCfg, homeCfg].find((p) => {
    try {
      return readFileSync(p), true;
    } catch {
      return false;
    }
  });
  if (!file) throw new Error("mcp.json not found – please create one under .cursor/");
  return JSON.parse(readFileSync(file!, "utf8"));
}

(async () => {
  const cfg = loadMcpConfig();
  // The names must match the keys under "mcpServers" inside mcp.json
  const githubName = process.env.GH_SERVER_NAME || "github";
  const slackName = process.env.SLACK_SERVER_NAME || "slack";
  const notionName = process.env.NOTION_SERVER_NAME || "notion";

  const github = new MCPSession(githubName); // client resolves via config
  const slack = new MCPSession(slackName);
  const notion = new MCPSession(notionName);

  const today = new Date().toISOString().slice(0, 10);

  // 1. collect commits
  const { owner, repo } = cfg.mcpServers[githubName].env; // pass owner/repo via env inside server
  const commits: any[] = await github.call("list_commits", {
    owner,
    repo,
    sha: "main",
    since: `${today}T00:00:00Z`,
  });
  const messages = commits.map((c) => `- ${c.commit.message.split("\n")[0]}`).join("\n");
  if (!messages) return;

  const report = `【${today}】\n${messages}`;

  // 2. slack
  await slack.call("slack_post_message", {
    channel_id: cfg.mcpServers[slackName].env.SLACK_CHANNEL_ID,
    text: report,
  });

  // 3. notion
  await notion.call("v1/pages", {
    parent: { database_id: cfg.mcpServers[notionName].env.NOTION_DB_ID },
    properties: {
      Date: { date: { start: today } },
      Summary: { rich_text: [{ text: { content: messages } }] },
    },
  });
})();