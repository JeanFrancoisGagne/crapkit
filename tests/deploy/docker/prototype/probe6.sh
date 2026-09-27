set -u
export HOME=/home/tester/h CODEX_HOME=/home/tester/h/.codex
mkdir -p $CODEX_HOME
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
hooks() {
  { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"probe","version":"0"}}}' '{"jsonrpc":"2.0","method":"initialized"}' "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"hooks/list\",\"params\":{\"cwds\":[\"$HOME\"]}}"; sleep 6; } | timeout 30 codex app-server 2>/dev/null | python3.12 -c "
import sys,json
for line in sys.stdin:
    try: m=json.loads(line)
    except Exception: continue
    if m.get('id')==2:
        r=m.get('result') or m.get('error'); s=json.dumps(r)
        d=r.get('data') if isinstance(r,dict) else None
        n=0
        def walk(x):
            global n
            if isinstance(x,dict):
                if 'command' in x and ('timeoutSec' in x or 'async' in x): n+=1
                for v in x.values(): walk(v)
            elif isinstance(x,list):
                for v in x: walk(v)
        walk(r); print('handlers', n, s[:300]); break
"
}
variant() {
  rm -rf $HOME/mk $CODEX_HOME/plugins $CODEX_HOME/config.toml; mkdir -p $HOME/mk && cp -r /src/.claude-plugin /src/plugin $HOME/mk/
  eval "$1"
  (cd $HOME/mk && git init -q -b main && git add -A && git -c user.email=t@t -c user.name=t commit -qm i)
  codex plugin marketplace add $HOME/mk >/dev/null 2>&1; codex plugin add crapkit@crapkit --json >/dev/null 2>&1
  echo "--- $2"; hooks
}
variant ":" "claude layout only"
variant "mkdir -p \$HOME/mk/plugin/.codex-plugin && printf '{\"name\":\"crapkit\",\"version\":\"0.8.0\",\"description\":\"x\"}' > \$HOME/mk/plugin/.codex-plugin/plugin.json" "codex manifest, no hooks key"
variant "mkdir -p \$HOME/mk/plugin/.codex-plugin && printf '{\"name\":\"crapkit\",\"version\":\"0.8.0\",\"description\":\"x\",\"hooks\":{}}' > \$HOME/mk/plugin/.codex-plugin/plugin.json" "codex manifest, hooks {}"
