// The MCP servers a Claude Agent SDK (TypeScript) session connects, as its
// own mcpServerStatus() reports them, with no model turn and no API key.
//
//   node sdk_status.mjs --sdk <package dir> --cwd <dir> --options <options.json> [--env no-path]
//
// options.json holds the SDK options under test (`plugins`, `mcpServers`, ...).
// The prompt is an async iterable that never yields, so the session starts,
// connects its MCP servers and waits for a first message that never comes.
// The script asks mcpServerStatus() until no server is `pending`, prints one
// JSON line, [{name, status, source, error, tools: [names]}], and exits.
// `--env no-path` passes options.env as this environment minus PATH: an SDK
// host that builds env by hand and leaves PATH out.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const POLL_MS = 100;
const BOUND_MS = 60000;

function args(argv) {
  const out = { env: "inherit" };
  for (let i = 2; i < argv.length; i += 2) out[argv[i].replace(/^--/, "")] = argv[i + 1];
  return out;
}

function withoutPath(env) {
  const copy = { ...env };
  for (const key of Object.keys(copy)) if (key.toUpperCase() === "PATH") delete copy[key];
  return copy;
}

const never = { [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}) }) };

function summary(status) {
  return status.map((server) => ({
    name: server.name, status: server.status, source: server.source ?? null, error: server.error ?? null,
    tools: (server.tools ?? []).map((tool) => tool.name),
  }));
}

async function settled(session) {
  const deadline = Date.now() + BOUND_MS;
  for (;;) {
    const status = await session.mcpServerStatus();
    if (!status.some((server) => server.status === "pending") || Date.now() > deadline) return status;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

const opts = args(process.argv);
const { query } = await import(pathToFileURL(join(opts.sdk, "sdk.mjs")).href);
const options = JSON.parse(readFileSync(opts.options, "utf8"));
options.cwd = opts.cwd;
if (opts.env === "no-path") options.env = withoutPath(process.env);
const session = query({ prompt: never, options });
console.log(JSON.stringify(summary(await settled(session))));
process.exit(0);
