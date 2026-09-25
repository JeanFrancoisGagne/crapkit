#!/bin/sh
# The deploy images' entry point. tools/deploy/run.py copies this file into
# <out>/in beside the exported tree and runs it as uid 1000:
#
#   docker run --rm --network none --user 1000:1000 -v <out>:/out -e CRAPKIT_DEPLOY=1 \
#     crapkit-deploy:<image> sh /out/in/entry.sh -m '<marker expression>' -n <N>
#
# Subcommands:
#   versions   one "<tool> <its --version output>" line per installed tool
#   manifest   versions, dpkg-query -W, npm ls per prefix, the runner's pip
#              freeze and a sha256 per wheel and per fetched binary tree
#   (default)  unpack /out/in, build the candidate, run pytest tests/deploy with
#              the remaining arguments; JUnit and transcripts land in /out
set -eu

RUNNER=/opt/runner/bin/python

first_line() {
    # The first line that carries a version, else the first line. A CLI that
    # hangs under --network none must not hang the listing. Copilot unpacks
    # itself on its first run in a new HOME and, when that is slow, prints
    # "Package extraction took 7426ms" first. Amp prints its release's age
    # ("6h ago"), which the manifest must not hold.
    HOME="$VERSION_HOME" timeout 60 "$@" 2>&1 </dev/null \
        | awk '/[0-9]+\.[0-9]+/ { print; found = 1; exit } NR == 1 { first = $0 } END { if (!found) print first }' \
        | sed -E 's/, [0-9]+[a-z]+ ago\)/)/' || true
}

versions() {
    # Codex refuses a CODEX_HOME under /tmp and prints that refusal first.
    VERSION_HOME=$(mktemp -d /work/.versions.XXXXXX)
    export VERSION_HOME DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        DISABLE_TELEMETRY=1 COPILOT_OFFLINE=true PATH="/opt/bun/bin:$PATH"
    echo "uv $(first_line uv --version)"
    echo "node $(first_line node --version)"
    echo "npm $(first_line npm --version)"
    echo "git $(first_line git --version)"
    echo "pipx $(first_line pipx --version)"
    echo "prek $(first_line prek --version)"
    for python in /opt/toolchain/bin/python3.*; do
        echo "$(basename "$python") $(first_line "$python" -c 'import platform; print(platform.python_version())')"
    done
    echo "system-python3 $(first_line /usr/bin/python3 --version)"
    for dir in /opt/harness-core/bin /opt/harness-full/bin /opt/act/bin; do
        [ -d "$dir" ] || continue
        for tool in "$dir"/*; do echo "$(basename "$tool") $(first_line "$tool" --version)"; done
    done
    # full-latest: the same commands at their newest releases.
    for tool in /opt/harness-latest/bin/*; do
        [ -e "$tool" ] && echo "$(basename "$tool")@latest $(first_line "$tool" --version)"
    done
    [ -x /opt/vscode/bin/code ] && echo "vscode $(first_line /opt/vscode/bin/code --version --no-sandbox)"
    [ -x /opt/zed/bin/zed ] && echo "zed $(first_line /opt/zed/bin/zed --version)"
    rm -rf "$VERSION_HOME"
}

tree_hash() {
    find "$1" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
}

manifest() {
    echo "## versions"; versions
    echo "## dpkg"; dpkg-query -W
    for prefix in /opt/npm-fixtures /opt/harness-core /opt/harness-full; do
        [ -f "$prefix/package-lock.json" ] || continue
        echo "## npm $prefix"
        node -e 'const l=require(process.argv[1]); for (const [k,v] of Object.entries(l.packages)) if (k) console.log(k.replace(/^node_modules\//,""), v.version||"", v.integrity||"")' \
            "$prefix/package-lock.json"
    done
    # The README's `npm i -D` lines run unlocked at build time; the tarballs they
    # cached are what an offline install in a cell gets, so a cold rebuild that
    # cached a newer release shows here.
    echo "## npm-cache"
    npm cache ls --cache /opt/npm-cache 2>/dev/null | sed -n 's#^make-fetch-happen:request-cache:https://registry.npmjs.org/##p' \
        | grep '\.tgz$' | LC_ALL=C sort
    echo "## runner"; "$RUNNER" -m pip freeze 2>/dev/null || uv pip freeze --python "$RUNNER"
    echo "## wheelhouse"; (cd /opt/wheelhouse && sha256sum -- * )
    echo "## binaries"
    for tree in /opt/cursor-agent /opt/goose /opt/bun /opt/act/bin /opt/junie-home /opt/vscode /opt/zed; do
        [ -d "$tree" ] && echo "$(tree_hash "$tree") $tree"
    done
    for file in /opt/toolchain/bin/pipx /opt/toolchain/bin/prek; do sha256sum "$file"; done
}

run_cells() {
    work=${CRAPKIT_DEPLOY_WORK:-/work}
    in=${CRAPKIT_DEPLOY_IN:-/out/in}
    mkdir -p "$work/src" "$work/tmp" /out/transcripts
    tar -xf "$in/tree.tar" -C "$work/src"
    # A detached worktree exports a detached HEAD; naming the default branch
    # keeps git's "Using 'master'" hint out of every run's log.
    git -c init.defaultBranch=main clone -q --mirror "$in/src.bundle" "$work/src.git"
    "$RUNNER" "$work/src/tools/deploy/candidate.py" --tree "$in/tree.tar" \
        --lock "$work/src/tools/deploy/wheelhouse.lock" --out "$work/candidate"
    export CRAPKIT_DEPLOY=1 PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 \
        CRAPKIT_DEPLOY_CANDIDATE="$work/candidate" CRAPKIT_DEPLOY_MIRROR="$work/src.git" \
        CRAPKIT_DEPLOY_OUT=/out CRAPKIT_DEPLOY_SRC="$work/src"
    cd "$work/src"
    exec "$RUNNER" -m pytest tests/deploy -p no:cacheprovider -p no:randomly --basetemp "$work/tmp" \
        --junitxml /out/junit.xml "$@"
}

case "${1:-}" in
    versions) versions ;;
    manifest) manifest ;;
    *) run_cells "$@" ;;
esac
