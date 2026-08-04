// Minimal .env fallback loader -- no dependency on the npm `dotenv` package.
// Only fills in vars NOT already present in process.env, so a value already
// exported in the shell always wins; this is purely a convenience for local
// dev so RENDER_API_KEY/UPSTASH_*/UPTIMEROBOT_API_KEY don't have to be
// manually exported before every `claude` start if they're already sitting
// in the repo's (gitignored) .env.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..");

export function loadDotenvFallback(path = join(REPO_ROOT, ".env")) {
  if (!existsSync(path)) return;
  const lines = readFileSync(path, "utf8").split("\n");
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    const quoted = (value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"));
    if (quoted) value = value.slice(1, -1);
    if (!(key in process.env)) process.env[key] = value;
  }
}

// Windows' cmd.exe (invoked via shell: true, needed because npm-installed
// binaries are .cmd shims that CreateProcess can't exec directly) has no
// safe array-of-args form -- Node just concatenates an args array as-is
// (see its own DEP0190 warning), which truncates any value containing a
// space, like "Authorization: Bearer <token>". Building one fully-quoted
// command string ourselves and passing it with no separate args array
// sidesteps both that truncation and the EINVAL from spawning a .cmd
// directly without a shell at all.
const ESCAPED_QUOTE = String.raw`\"`;

export function quoteArg(arg) {
  return `"${String(arg).replaceAll('"', ESCAPED_QUOTE)}"`;
}

export function buildShellCommand(command, args) {
  // The command name itself stays unquoted: quoting it suppresses cmd.exe's
  // PATH/PATHEXT resolution (the reason plain "npx" finds npx.cmd), which
  // silently breaks the lookup. Only args need quoting, for the ones
  // (like an "Authorization: Bearer <token>" header) containing spaces.
  return [command, ...args.map(quoteArg)].join(" ");
}
