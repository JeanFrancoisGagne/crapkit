set -u
export HOME=/home/tester/h CODEX_HOME=/home/tester/h/.codex XDG_CONFIG_HOME=/home/tester/h/.config XDG_DATA_HOME=/home/tester/h/.local/share XDG_CACHE_HOME=/home/tester/h/.cache
mkdir -p $CODEX_HOME
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
echo "== opencode"; printf '{"mcp":{"crapkit":{"type":"local","command":["crapkit","mcp"]}}}' > $HOME/oc.json; OPENCODE_CONFIG=$HOME/oc.json timeout 60 opencode mcp list 2>&1 | cat -v | tail -6
echo "== codex app-server"; codex mcp add crapkit -- crapkit mcp >/dev/null 2>&1
t0=$(date +%s%N)
{ printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"0"}}}' '{"jsonrpc":"2.0","method":"initialized"}' '{"jsonrpc":"2.0","id":2,"method":"mcpServerStatus/list","params":{"detail":"toolsAndAuthOnly"}}'; sleep 8; } | timeout 30 codex app-server 2>/dev/null | python3.12 -c "
import sys,json
for line in sys.stdin:
    try: m=json.loads(line)
    except Exception: continue
    if m.get('id')==2:
        r=m.get('result',m.get('error'))
        s=json.dumps(r); print(s[:500]); break
"
echo "ms=$(( ($(date +%s%N)-t0)/1000000 ))"
