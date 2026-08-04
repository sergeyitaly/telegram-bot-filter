#!/usr/bin/env node
// Telegram Bot API MCP server — no external dependencies required.
// Uses Node 18+ built-in fetch and implements JSON-RPC/MCP over stdio.
// Set BOT_TOKEN (or TELEGRAM_BOT_TOKEN) in the environment before starting,
// or leave it in the repo's (gitignored) .env — loadDotenvFallback below
// picks up whatever isn't already set in the shell.
import { loadDotenvFallback } from "./dotenv-util.mjs";

loadDotenvFallback();

const TOKEN = process.env.BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN || "";
const BASE = `https://api.telegram.org/bot${TOKEN}`;

async function tg(method, body = {}) {
  const r = await fetch(`${BASE}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

const TOOLS = [
  {
    name: "tg_get_me",
    description: "Verify bot identity and check it is connected to Telegram",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "tg_get_chat",
    description: "Get full chat info including current permissions — useful for checking whether lockdown is applied",
    inputSchema: {
      type: "object",
      required: ["chat_id"],
      properties: { chat_id: { type: "string", description: "Numeric chat id (e.g. -1001234567890) or @username" } },
    },
  },
  {
    name: "tg_get_chat_administrators",
    description: "List all administrators of a chat",
    inputSchema: {
      type: "object",
      required: ["chat_id"],
      properties: { chat_id: { type: "string" } },
    },
  },
  {
    name: "tg_get_chat_member",
    description: "Check a specific member's status and permissions in a chat",
    inputSchema: {
      type: "object",
      required: ["chat_id", "user_id"],
      properties: {
        chat_id: { type: "string" },
        user_id: { type: "integer", description: "Numeric Telegram user ID" },
      },
    },
  },
  {
    name: "tg_send_message",
    description: "Send a text message to a chat (use for testing only — this goes through the live bot token)",
    inputSchema: {
      type: "object",
      required: ["chat_id", "text"],
      properties: {
        chat_id: { type: "string" },
        text: { type: "string" },
      },
    },
  },
  {
    name: "tg_get_updates",
    description: "Fetch recent updates (messages/events) received by the bot — note: long-polling bot must be stopped first",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "integer", default: 10 },
        offset: { type: "integer" },
      },
    },
  },
];

async function handle(name, args) {
  switch (name) {
    case "tg_get_me":
      return tg("getMe");
    case "tg_get_chat":
      return tg("getChat", { chat_id: args.chat_id });
    case "tg_get_chat_administrators":
      return tg("getChatAdministrators", { chat_id: args.chat_id });
    case "tg_get_chat_member":
      return tg("getChatMember", { chat_id: args.chat_id, user_id: args.user_id });
    case "tg_send_message":
      return tg("sendMessage", { chat_id: args.chat_id, text: args.text });
    case "tg_get_updates":
      return tg("getUpdates", { limit: args.limit ?? 10, ...(args.offset ? { offset: args.offset } : {}) });
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

// ── MCP JSON-RPC 2.0 over stdio ──────────────────────────────────────────────

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  const lines = buf.split("\n");
  buf = lines.pop();
  for (const line of lines) {
    if (!line.trim()) continue;
    let req;
    try { req = JSON.parse(line); } catch { continue; }
    handleRequest(req);
  }
});

async function handleRequest(req) {
  const { id, method, params } = req;
  // Notifications have no id and need no response
  if (id === undefined) return;
  try {
    let result;
    if (method === "initialize") {
      result = {
        protocolVersion: "2024-11-05",
        serverInfo: { name: "telegram-bot-api", version: "1.0.0" },
        capabilities: { tools: {} },
      };
    } else if (method === "tools/list") {
      result = { tools: TOOLS };
    } else if (method === "tools/call") {
      const out = await handle(params.name, params.arguments || {});
      result = { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] };
    } else {
      throw { code: -32601, message: `Method not found: ${method}` };
    }
    send({ jsonrpc: "2.0", id, result });
  } catch (err) {
    send({ jsonrpc: "2.0", id, error: { code: err.code ?? -32603, message: String(err.message ?? err) } });
  }
}

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
