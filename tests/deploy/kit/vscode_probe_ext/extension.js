// Test-only probe for lin-vscode-electron. Once VS Code finishes starting, it
// starts every configured MCP server the way the Start code lens in mcp.json
// does (autoTrustChanges: the user asked for this server). VS Code reads the
// mcp.json files after startup finishes, so the probe asks again every two
// seconds until the language-model tool list holds $CRAPKIT_PROBE_EXPECT
// crapkit tools or the deadline passes. It then writes what it saw to
// $CRAPKIT_PROBE_OUT as JSON and quits.
const fs = require("fs");
const vscode = require("vscode");

const START = { waitForLiveTools: true, autoTrustChanges: true };

function crapkitTools() {
  return vscode.lm.tools.map((tool) => tool.name).filter((name) => /crapkit/i.test(name));
}

function expected() {
  return Number(process.env.CRAPKIT_PROBE_EXPECT || "12");
}

function start(errors) {
  vscode.commands.executeCommand("workbench.mcp.startServer", "*", START).then(
    () => undefined, (error) => errors.push(String(error)));
}

function settle(deadline, errors) {
  return new Promise((resolve) => {
    let ticks = 0;
    const tick = () => {
      if (crapkitTools().length >= expected() || Date.now() > deadline) return resolve();
      if (ticks++ % 8 === 0) start(errors);
      setTimeout(tick, 250);
    };
    tick();
  });
}

async function activate() {
  const seconds = Number(process.env.CRAPKIT_PROBE_SECONDS || "90");
  const errors = [];
  await settle(Date.now() + seconds * 1000, errors);
  const report = { tools: vscode.lm.tools.map((tool) => tool.name), crapkit: crapkitTools(), errors };
  fs.writeFileSync(process.env.CRAPKIT_PROBE_OUT, JSON.stringify(report));
  await vscode.commands.executeCommand("workbench.action.quit");
}

module.exports = { activate, deactivate() {} };
