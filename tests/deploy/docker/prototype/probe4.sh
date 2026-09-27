set -u
export HOME=/home/tester/h; mkdir -p $HOME
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
mkdir -p $HOME/cursor && cd $HOME/cursor && curl -fsSL https://downloads.cursor.com/lab/2026.09.23-86fc751/linux/x64/agent-cli-package.tar.gz | tar xz && ls $HOME/cursor | head; A=$(find $HOME/cursor -maxdepth 3 -name 'cursor-agent' -o -maxdepth 3 -name agent -type f | head -1); echo "bin=$A"; du -sh $HOME/cursor
mkdir -p $HOME/proj/.cursor && cd $HOME/proj && git init -q -b main && printf '{"mcpServers":{"crapkit":{"type":"stdio","command":"crapkit","args":["mcp","--repo","${workspaceFolder}"]}}}' > .cursor/mcp.json
timeout 60 $A --version 2>&1 | tail -1
echo "== list-tools"; timeout 60 $A mcp list-tools crapkit 2>&1 | tail -15
echo "== copilot get"; mkdir -p $HOME/.copilot; printf '{"mcpServers":{"crapkit":{"type":"local","command":"crapkit","args":["mcp"],"tools":["*"]}}}' > $HOME/.copilot/mcp-config.json; COPILOT_OFFLINE=true COPILOT_MCP_TOOL_CACHE=false timeout 60 copilot mcp get crapkit --json 2>&1 | head -c 700
