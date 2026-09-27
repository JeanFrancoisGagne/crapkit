#!/bin/sh
# What an image serves offline, checked the way a user runs it: pipx installed
# with pip, crapkit[py] and pytest into a venv of every CPython the image holds
# (the 3.15 prerelease included), the README's vitest provider line against the
# npm cache, and the Cursor agent under both names when the image holds it.
#
#   docker run --rm --network none --user 1000:1000 -v <tree>:/src:ro \
#       crapkit-deploy:<image> sh /src/tests/deploy/docker/prototype/probe_serves.sh
#
# One line per probe: <probe>|<exit>|<ms>|<pass or FAIL>|<last line of output>
set -u
HOME=$(mktemp -d /work/.serves.XXXXXX)
export HOME
printf '[global]\nno-index = true\nfind-links = /opt/wheelhouse\n' > "$HOME/pip.conf"
export PIP_CONFIG_FILE="$HOME/pip.conf" UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS=/opt/wheelhouse \
    npm_config_cache=/opt/npm-cache npm_config_offline=true npm_config_update_notifier=false \
    npm_config_fund=false npm_config_audit=false
N1=$(ls /opt/wheelhouse | sed -n 's/^crapkit-\([0-9.]*\)-py3-none-any.whl$/\1/p' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)

probe() {
    # probe NAME PATTERN COMMAND...: pass when the command exits 0 and its output matches PATTERN.
    name=$1 pattern=$2; shift 2
    started=$(date +%s%N)
    out=$(timeout 300 "$@" 2>&1 </dev/null); code=$?
    ms=$(( ($(date +%s%N) - started) / 1000000 ))
    verdict=FAIL; [ "$code" = 0 ] && echo "$out" | grep -Eq "$pattern" && verdict=pass
    echo "$name|$code|$ms|$verdict|$(echo "$out" | tr -d '\r' | grep -v '^\s*$' | tail -1 | cut -c1-160)"
}

echo "crapkit $N1 is the newest release in the wheelhouse; $(uname -m)"
python3.12 -m venv "$HOME/pipx-venv"
probe pipx-from-pip "Successfully installed" "$HOME/pipx-venv/bin/pip" install pipx
probe pipx-runs "^[0-9]" "$HOME/pipx-venv/bin/pipx" --version

for python in /opt/toolchain/bin/python3.1[1-9]; do
    minor=$(basename "$python" | sed 's/python//')
    "$python" -m venv "$HOME/py$minor"
    probe "crapkit-py-$minor" "Successfully installed" "$HOME/py$minor/bin/pip" install "crapkit[py]==$N1" pytest
    probe "crapkit-version-$minor" "crapkit $N1" "$HOME/py$minor/bin/crapkit" --version
done

vitest=$(node -p "require('/opt/npm-fixtures/package.json').devDependencies.vitest")
mkdir -p "$HOME/ts" && cd "$HOME/ts" && npm init -y >/dev/null
probe npm-vitest "added" npm i -D "vitest@$vitest"
probe npm-readme-provider "added" npm i -D "@vitest/coverage-v8@${vitest%%.*}"

if [ -x /opt/harness-core/bin/agent ]; then
    probe cursor-agent-version "^20[0-9][0-9]\." /opt/harness-core/bin/cursor-agent --version
    probe agent-version "^20[0-9][0-9]\." /opt/harness-core/bin/agent --version
fi
