set -u
export HOME=/home/tester/h CODEX_HOME=/home/tester/h/.codex CLAUDE_CONFIG_DIR=/home/tester/h/.claude DISABLE_AUTOUPDATER=1
mkdir -p $CODEX_HOME $CLAUDE_CONFIG_DIR
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
mkdir -p $HOME/mk && cp -r /src/.claude-plugin /src/plugin $HOME/mk/ && mkdir -p $HOME/mk/plugin/.codex-plugin && printf '{"name":"crapkit","version":"0.8.0","description":"x","hooks":{}}' > $HOME/mk/plugin/.codex-plugin/plugin.json
(cd $HOME/mk && git init -q -b main && git add -A && git -c user.email=t@t -c user.name=t commit -qm i)
codex plugin marketplace add $HOME/mk >/dev/null 2>&1; codex plugin add crapkit@crapkit --json >/dev/null 2>&1
{ printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"0"}}}' '{"jsonrpc":"2.0","method":"initialized"}' '{"jsonrpc":"2.0","id":2,"method":"mcpServerStatus/list","params":{"detail":"toolsAndAuthOnly"}}' "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"skills/list\",\"params\":{\"cwds\":[\"$HOME\"]}}"; sleep 8; } | timeout 30 codex app-server 2>/dev/null | python3.12 -c "
import sys,json
seen=set()
for line in sys.stdin:
    try: m=json.loads(line)
    except Exception: continue
    if m.get('id')==2:
        d=m['result']['data']; print('mcp', [(x['name'], len(x.get('tools') or {})) for x in d]); seen.add(2)
    if m.get('id')==3:
        s=json.dumps(m.get('result')); import re; print('skills', sorted(set(re.findall(r'crapkit:[a-z-]+', s)))); seen.add(3)
    if seen=={2,3}: break
"
echo "== claude reads the same marketplace"; claude plugin marketplace add $HOME/mk >/dev/null 2>&1; claude plugin install crapkit@crapkit --json 2>&1 | tail -1 | cut -c1-120; claude plugin list --json 2>&1 | python3.12 -c "import sys,json; d=json.load(sys.stdin); print([(p.get('id') or p.get('name'), p.get('version'), p.get('errors')) for p in (d if isinstance(d,list) else d.get('plugins',[]))])" 2>&1 | head -3
