#!/usr/bin/env node
// Launches the official Upstash MCP server (npx @upstash/mcp-server).
// UPSTASH_EMAIL/UPSTASH_API_KEY are read from the shell environment first,
// falling back to a project-root .env file if either is missing there.
import { spawn } from "node:child_process";
import { buildShellCommand, loadDotenvFallback } from "./dotenv-util.mjs";

loadDotenvFallback();

const email = process.env.UPSTASH_EMAIL;
const apiKey = process.env.UPSTASH_API_KEY;
if (!email || !apiKey) {
  console.error("upstash MCP: missing UPSTASH_EMAIL/UPSTASH_API_KEY (set in shell or repo .env)");
  process.exit(1);
}

// shell: true is required on Windows (npm-installed binaries are .cmd
// shims; spawning them without a shell throws EINVAL), but Node's shell
// mode just concatenates an args array with no escaping (see its DEP0190
// warning) -- so the command is pre-quoted into one string ourselves
// instead of passing a separate args array for Node to mangle.
const command = buildShellCommand("npx", ["-y", "@upstash/mcp-server@latest", "--email", email, "--api-key", apiKey]);
const child = spawn(command, { stdio: "inherit", shell: true });
child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (err) => {
  console.error("upstash MCP: failed to launch npx:", err.message);
  process.exit(1);
});
