set -u
export HOME=/home/tester/h CODEX_HOME=/home/tester/h/.codex CLAUDE_CONFIG_DIR=/home/tester/h/.claude DISABLE_AUTOUPDATER=1
mkdir -p $CODEX_HOME $CLAUDE_CONFIG_DIR
uv venv -q -p 3.12 $HOME/v && VIRTUAL_ENV=$HOME/v uv pip install -q crapkit==0.8.0
export PATH=$HOME/v/bin:$PATH
mkdir -p $HOME/mk && cp -r /src/.claude-plugin /src/plugin $HOME/mk/ && cd $HOME/mk && git init -q -b main && git add -A && git -c user.email=t@t -c user.name=t commit -qm i && cd
echo "== claude plugin"; claude plugin marketplace add $HOME/mk 2>&1 | tail -2; claude plugin install crapkit@crapkit --json 2>&1 | tail -1; timeout 60 claude mcp list 2>&1 | tail -2; crapkit doctor --plugin-root; echo "doctor exit=$?"
ls $CLAUDE_CONFIG_DIR/plugins/cache/crapkit/crapkit/ 2>&1
echo "== codex plugin"; codex plugin marketplace add $HOME/mk 2>&1 | grep -v WARNING | tail -2; codex plugin add crapkit@crapkit --json 2>&1 | grep -v WARNING | tail -2; codex plugin list --marketplace crapkit --json 2>&1 | grep -v WARNING | head -c 400; echo
ls -d $CODEX_HOME/plugins/cache/crapkit/crapkit/* 2>&1; crapkit doctor --plugin-root $(ls -d $CODEX_HOME/plugins/cache/crapkit/crapkit/* | head -1); echo "doctor exit=$?"
