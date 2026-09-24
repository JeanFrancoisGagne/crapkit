#!/bin/sh
# probe.sh to probe7.sh again, inside the pinned :full image, with no network.
#
#   docker run --rm --network none -v <tree>:/src:ro crapkit-deploy:full sh /src/tests/deploy/docker/prototype/probe_offline.sh
#
# The first run of these probes fetched crapkit from PyPI and the Cursor agent
# from its CDN, with the network on, so "no login needed" and every timing held
# only online. Here crapkit comes from the image's wheelhouse, every CLI from
# the image, and every telemetry and update switch is off. One line per probe:
#   <probe>|<exit>|<ms>|<pass|FAIL>|<last line of output>
set -u
N1=$(ls /opt/wheelhouse | sed -n 's/^crapkit-\([0-9.]*\)-py3-none-any.whl$/\1/p' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
# Outside /tmp: Codex refuses a CODEX_HOME there. HOME is set on its own line
# because one export expands every value before it assigns any.
HOME=$(mktemp -d /work/.probe.XXXXXX)
export HOME CODEX_HOME="$HOME/.codex" CLAUDE_CONFIG_DIR="$HOME/.claude" GEMINI_CLI_HOME="$HOME" \
    XDG_CONFIG_HOME="$HOME/.config" XDG_DATA_HOME="$HOME/.local/share" XDG_CACHE_HOME="$HOME/.cache" \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_TELEMETRY=1 COPILOT_OFFLINE=true \
    GEMINI_TELEMETRY_ENABLED=false GEMINI_CLI_TRUST_WORKSPACE=true \
    UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=/opt/wheelhouse
mkdir -p "$CODEX_HOME" "$CLAUDE_CONFIG_DIR" "$HOME/.gemini" "$HOME/.copilot"
printf '{"general":{"disableAutoUpdate":true},"privacy":{"usageStatisticsEnabled":false},"telemetry":{"enabled":false}}\n' \
    > "$HOME/.gemini/settings.json"
uv venv -q -p 3.12 "$HOME/v" && VIRTUAL_ENV="$HOME/v" uv pip install -q "crapkit==$N1" || exit 1
export PATH="$HOME/v/bin:/opt/harness-core/bin:/opt/harness-full/bin:/opt/bun/bin:$PATH"
mkdir -p "$HOME/r" && cd "$HOME/r" && git init -q -b main && printf 'def f(x):\n    return x\n' > a.py \
    && git add -A && git -c user.email=t@t -c user.name=t commit -qm i

probe() {
    # probe NAME PATTERN COMMAND...: pass when the output matches PATTERN.
    name=$1 pattern=$2; shift 2
    started=$(date +%s%N)
    out=$(timeout 90 "$@" 2>&1 </dev/null); code=$?
    ms=$(( ($(date +%s%N) - started) / 1000000 ))
    verdict=FAIL; echo "$out" | grep -Eq "$pattern" && verdict=pass
    echo "$name|$code|$ms|$verdict|$(echo "$out" | tr -d '\r' | grep -v '^\s*$' | tail -1 | cut -c1-160)"
}

# One codex app-server session: initialize, then one request, waiting for its reply.
cat > "$HOME/app_server.py" <<'EOF'
import json, subprocess, sys
method, params = sys.argv[1], json.loads(sys.argv[2])
server = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
def send(message):
    server.stdin.write(json.dumps(message) + "\n"); server.stdin.flush()
send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "probe", "version": "0"}}})
send({"jsonrpc": "2.0", "method": "initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
for line in server.stdout:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if message.get("id") == 2:
        print(json.dumps(message.get("result", message.get("error")))[:2000]); break
server.kill()
EOF

echo "crapkit $N1 from the wheelhouse"
claude mcp add --scope user crapkit -- crapkit mcp >/dev/null 2>&1
probe claude-mcp-list "Connected" claude mcp list
codex mcp add crapkit -- crapkit mcp >/dev/null 2>&1
probe codex-mcp-status '"name": "crapkit"' python3.12 "$HOME/app_server.py" mcpServerStatus/list '{"detail":"toolsAndAuthOnly"}'
gemini mcp add -s user crapkit crapkit mcp >/dev/null 2>&1
probe gemini-mcp-list "Connected" gemini mcp list
printf '{"$schema":"https://opencode.ai/config.json","autoupdate":false,"mcp":{"crapkit":{"type":"local","command":["crapkit","mcp"]}}}\n' > "$HOME/oc.json"
probe opencode-mcp-list "connected" env OPENCODE_CONFIG="$HOME/oc.json" opencode mcp list
printf '{"mcpServers":{"crapkit":{"type":"local","command":"crapkit","args":["mcp"],"tools":["*"]}}}\n' > "$HOME/.copilot/mcp-config.json"
probe copilot-mcp-get '"crapkit"' copilot mcp get crapkit --json
probe cline-mcp-install '"crapkit"' cline mcp install crapkit --yes --json -- crapkit mcp
probe cline-config-mcp '"crapkit"' cline config mcp --json
mkdir -p .cursor && printf '{"mcpServers":{"crapkit":{"type":"stdio","command":"crapkit","args":["mcp","--repo","%s"]}}}\n' "$PWD" > .cursor/mcp.json
cursor-agent mcp enable crapkit >/dev/null 2>&1
probe cursor-list-tools "get_next_item" cursor-agent mcp list-tools crapkit
if [ -d /src/plugin ]; then
    mkdir -p "$HOME/mk" && cp -r /src/.claude-plugin /src/plugin "$HOME/mk/"
    (cd "$HOME/mk" && git init -q -b main && git add -A && git -c user.email=t@t -c user.name=t commit -qm i)
    claude plugin marketplace add "$HOME/mk" >/dev/null 2>&1
    probe claude-plugin-install "crapkit" claude plugin install crapkit@crapkit
    probe claude-plugin-doctor "checking" crapkit doctor --plugin-root
    codex plugin marketplace add "$HOME/mk" >/dev/null 2>&1
    probe codex-plugin-add "crapkit" codex plugin add crapkit@crapkit --json
    probe codex-hooks-list '"command"' python3.12 "$HOME/app_server.py" hooks/list "{\"cwds\":[\"$HOME\"]}"
    probe codex-skills-list "crapkit" python3.12 "$HOME/app_server.py" skills/list "{\"cwds\":[\"$HOME\"]}"
fi
