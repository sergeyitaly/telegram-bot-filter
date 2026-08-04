#!/usr/bin/env node
// Bridges UptimeRobot's hosted MCP server (mcp.uptimerobot.com) to stdio via
// the `mcp-remote` package. Needs the Main API key (not the read-only one
// bot/uptime_check.py uses), since MCP tools can create/modify monitors.
// UPTIMEROBOT_API_KEY is read from the shell environment first, falling
// back to a project-root .env file if missing there.
import { spawn } from "node:child_process";
import { buildShellCommand, loadDotenvFallback } from "./dotenv-util.mjs";

loadDotenvFallback();

const apiKey = process.env.UPTIMEROBOT_API_KEY;
if (!apiKey) {
  console.error("uptimerobot MCP: missing UPTIMEROBOT_API_KEY (set in shell or repo .env)");
  process.exit(1);
}

// shell: true is required on Windows (npm-installed binaries are .cmd
// shims; spawning them without a shell throws EINVAL), but Node's shell
// mode just concatenates an args array with no escaping (see its DEP0190
// warning) -- so the command is pre-quoted into one string ourselves
// instead of passing a separate args array for Node to mangle (which
// otherwise truncates "--header" at the first space).
const command = buildShellCommand("npx", [
  "-y",
  "mcp-remote",
  "https://mcp.uptimerobot.com/mcp",
  "--header",
  `Authorization: Bearer ${apiKey}`,
]);
const child = spawn(command, { stdio: "inherit", shell: true });
child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (err) => {
  console.error("uptimerobot MCP: failed to launch npx:", err.message);
  process.exit(1);
});
