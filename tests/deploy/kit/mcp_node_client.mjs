// Drive an MCP server through the TypeScript SDK a harness profile ships.
//
//   node mcp_node_client.mjs --fixtures DIR --sdk sdk-1-25 --command crapkit
//        [--arg mcp ...] [--cwd DIR] [--env default|inherit] [--call NAME --arguments JSON]
//
// DIR holds the npm-fixtures install, where each SDK minor sits under its own
// alias (sdk-1-12, sdk-1-23, sdk-1-25, sdk-1-29), so the client's AJV schema
// checks are that version's own. `--env default` spawns the server with the
// SDK's getDefaultEnvironment(), which is what most TypeScript harnesses pass;
// `inherit` hands it this process's environment. Prints one JSON object:
// {protocolVersion, serverInfo, tools: [names], call?: result}.
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

function options(argv) {
  const parsed = { arg: [], env: "default" };
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, "");
    if (key === "arg") parsed.arg.push(argv[i + 1]);
    else parsed[key] = argv[i + 1];
  }
  return parsed;
}

async function load(fixtures, sdk, entry) {
  const require = createRequire(path.join(fixtures, "package.json"));
  return import(pathToFileURL(require.resolve(`${sdk}/${entry}`)).href);
}

async function main() {
  const opts = options(process.argv.slice(2));
  const { Client } = await load(opts.fixtures, opts.sdk, "client/index.js");
  const stdio = await load(opts.fixtures, opts.sdk, "client/stdio.js");
  const env = opts.env === "inherit" ? { ...process.env } : stdio.getDefaultEnvironment();
  const transport = new stdio.StdioClientTransport({
    command: opts.command, args: opts.arg, cwd: opts.cwd, env, stderr: "inherit",
  });
  const client = new Client({ name: "crapkit-deploy-kit", version: "1" }, { capabilities: {} });
  await client.connect(transport);
  const out = {
    protocolVersion: transport._protocolVersion ?? null,
    serverInfo: client.getServerVersion(),
    tools: (await client.listTools()).tools.map((tool) => tool.name),
  };
  if (opts.call) {
    out.call = await client.callTool({ name: opts.call, arguments: JSON.parse(opts.arguments ?? "{}") });
  }
  await client.close();
  process.stdout.write(JSON.stringify(out) + "\n");
}

main().catch((error) => {
  process.stderr.write(`mcp_node_client: ${error.stack ?? error}\n`);
  process.exit(1);
});
