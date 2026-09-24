set -u
export HOME=/home/tester/h; mkdir -p $HOME
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
mkdir -p $HOME/cursor && cd $HOME/cursor && curl -fsSL https://downloads.cursor.com/lab/2026.09.23-86fc751/linux/x64/agent-cli-package.tar.gz | tar xz; A=$HOME/cursor/dist-package/cursor-agent
mkdir -p $HOME/proj/.cursor && cd $HOME/proj && git init -q -b main && printf '[crapkit]\n' >/dev/null
printf '{"mcpServers":{"crapkit":{"type":"stdio","command":"sh","args":["-c","pwd > /home/tester/h/cwd.txt; env | sort > /home/tester/h/env.txt; exec crapkit mcp --repo \\"$0\\"","${workspaceFolder}"]}}}' > .cursor/mcp.json
cat .cursor/mcp.json; echo
timeout 60 $A mcp enable crapkit 2>&1 | tail -3
t0=$(date +%s%N); timeout 60 $A mcp list-tools crapkit 2>&1 | tail -14; echo "ms=$(( ($(date +%s%N)-t0)/1000000 ))"
echo "cwd=$(cat $HOME/cwd.txt 2>&1)"; wc -l < $HOME/env.txt 2>&1; grep -E '^(PATH|HOME|CURSOR|VIRTUAL_ENV)' $HOME/env.txt | cut -c1-120
