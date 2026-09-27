set -u
export HOME=/tmp/h CODEX_HOME=/tmp/h/.codex CLAUDE_CONFIG_DIR=/tmp/h/.claude DISABLE_AUTOUPDATER=1 GEMINI_CLI_HOME=/tmp/h GEMINI_CLI_TRUST_WORKSPACE=true XDG_CONFIG_HOME=/tmp/h/.config XDG_DATA_HOME=/tmp/h/.local/share XDG_CACHE_HOME=/tmp/h/.cache
mkdir -p $CODEX_HOME $CLAUDE_CONFIG_DIR
uv venv -q -p 3.12 /tmp/v && VIRTUAL_ENV=/tmp/v uv pip install -q crapkit==0.8.0
export PATH=/tmp/v/bin:$PATH
crapkit --version
mkdir -p /tmp/r && cd /tmp/r && git init -q -b main && printf 'def f(x):\n    return x\n' > a.py && git add -A && git -c user.email=t@t -c user.name=t commit -qm i
echo "== claude"; t0=$(date +%s%N); claude mcp add --scope user crapkit -- crapkit mcp >/dev/null 2>&1; timeout 60 claude mcp list 2>&1 | tail -2; echo "ms=$(( ($(date +%s%N)-t0)/1000000 ))"
echo "== codex"; codex mcp add crapkit -- crapkit mcp >/dev/null 2>&1; timeout 30 codex mcp list 2>&1 | tail -3
echo "== gemini"; t0=$(date +%s%N); gemini mcp add -s user crapkit crapkit mcp >/dev/null 2>&1; timeout 60 gemini mcp list 2>&1 | tail -2; echo "ms=$(( ($(date +%s%N)-t0)/1000000 ))"
echo "== opencode"; printf '{"mcp":{"crapkit":{"type":"local","command":["crapkit","mcp"]}}}' > /tmp/oc.json; t0=$(date +%s%N); OPENCODE_CONFIG=/tmp/oc.json timeout 60 opencode mcp list 2>&1 | tail -3; echo "ms=$(( ($(date +%s%N)-t0)/1000000 ))"
echo "== copilot"; mkdir -p $HOME/.copilot; printf '{"mcpServers":{"crapkit":{"type":"local","command":"crapkit","args":["mcp"],"tools":["*"]}}}' > $HOME/.copilot/mcp-config.json; COPILOT_OFFLINE=true timeout 60 copilot mcp list --json 2>&1 | head -c 600; echo
echo "== cline"; timeout 60 cline mcp install crapkit --yes --json -- crapkit mcp 2>&1 | tail -c 400; timeout 30 cline config mcp --json 2>&1 | tail -c 400; echo
