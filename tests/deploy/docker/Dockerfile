# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# The deploy-suite images. Every ARG comes from tools/deploy/pins.toml through
# tools/deploy/run.py, which is the one way to build them:
#
#     python tools/deploy/run.py --build-only --image core
#
# Targets, each a superset of the one before it where the name says so:
#   cells  base toolchain, npm fixture cache, runner venv, wheelhouse
#   core   cells + Claude Code, Codex (with floors) and the Cursor agent
#   full   core + every other pinned harness CLI
#   ci     cells + act, pre-fetched actions and a runner tool-cache Python
#   gui    full + xvfb, VS Code and Zed
#
# The wheelhouse layer is the last instruction of every target, so a new crapkit
# release rebuilds one small layer and nothing under it. Nothing here writes
# /etc/gitconfig: the sandbox's own $HOME/.gitconfig is the only git config a
# cell sees. PYTHONHASHSEED is set on the runner process by entry.sh, never here.

ARG DEBIAN_IMAGE
ARG UV_IMAGE
ARG NODE_IMAGE

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node

# ---------------------------------------------------------------- base ------
FROM ${DEBIAN_IMAGE} AS base
ARG SNAPSHOT
ARG PYTHONS
ARG PYTHON_OLD
ARG PYTHON_PRERELEASE
ARG PREK_LINUX_X64_URL
ARG PREK_LINUX_X64_SHA256
ARG PIPX_PYZ_URL
ARG PIPX_PYZ_SHA256
ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_DOWNLOADS=never UV_NO_PROGRESS=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 \
    CRAPKIT_DEPLOY_TOOLCHAIN=/opt/deploy/toolchain.json \
    PATH=/opt/runner/bin:/opt/toolchain/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SHELL ["/bin/sh", "-eu", "-c"]
RUN rm -f /etc/apt/sources.list.d/debian.sources; \
    printf 'deb http://snapshot.debian.org/archive/debian/%s trixie main\ndeb http://snapshot.debian.org/archive/debian-security/%s trixie-security main\n' \
      "$SNAPSHOT" "$SNAPSHOT" > /etc/apt/sources.list; \
    printf 'Acquire::Retries "5";\nAcquire::Check-Valid-Until "false";\nAcquire::http::Timeout "60";\n' > /etc/apt/apt.conf.d/80deploy; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl git xz-utils bzip2 unzip procps \
      python3 python3-pip python3-venv faketime; \
    rm -rf /var/lib/apt/lists/* /etc/gitconfig
# fetch URL SHA256 DEST: the one way a stage downloads a file.
COPY --chmod=755 <<'EOF' /usr/local/bin/fetch
#!/bin/sh
set -eu
curl -fsSL --retry 5 --retry-all-errors -o "$3" "$1"
echo "$2  $3" | sha256sum -c --quiet || { echo "fetch: $1 does not match its pins.toml sha256" >&2; exit 1; }
EOF
COPY --from=uv /uv /uvx /opt/toolchain/bin/
RUN UV_PYTHON_DOWNLOADS=automatic /opt/toolchain/bin/uv python install $PYTHON_OLD $PYTHONS $PYTHON_PRERELEASE; \
    for version in $PYTHON_OLD $PYTHONS $PYTHON_PRERELEASE; do \
      minor=$(echo "$version" | cut -d. -f1,2 | sed 's/rc.*//'); \
      ln -s "$(/opt/toolchain/bin/uv python find "$version")" "/opt/toolchain/bin/python$minor"; \
    done
COPY --from=node /usr/local/bin/node /opt/node/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /opt/node/lib/node_modules/npm
RUN ln -s /opt/node/bin/node /opt/toolchain/bin/node; \
    ln -s /opt/node/lib/node_modules/npm/bin/npm-cli.js /opt/toolchain/bin/npm; \
    ln -s /opt/node/lib/node_modules/npm/bin/npx-cli.js /opt/toolchain/bin/npx
RUN fetch "$PIPX_PYZ_URL" "$PIPX_PYZ_SHA256" /opt/toolchain/bin/pipx; chmod 755 /opt/toolchain/bin/pipx; \
    fetch "$PREK_LINUX_X64_URL" "$PREK_LINUX_X64_SHA256" /tmp/prek.tar.gz; \
    tar -xzf /tmp/prek.tar.gz -C /tmp; install -m 755 /tmp/prek-*/prek /opt/toolchain/bin/prek; rm -rf /tmp/prek*
RUN useradd --create-home --uid 1000 --shell /bin/sh tester; \
    install -d -o 1000 -g 1000 /work /out /opt/npm-cache; install -d -m 755 /opt/deploy
COPY --chmod=644 <<'EOF' /opt/deploy/toolchain.json
{
  "os": "linux",
  "path": ["/opt/toolchain/bin", "/usr/bin", "/bin"],
  "git": "/usr/bin/git",
  "uv": "/opt/toolchain/bin/uv",
  "uvx": "/opt/toolchain/bin/uvx",
  "node": "/opt/toolchain/bin/node",
  "npm": "/opt/toolchain/bin/npm",
  "pipx": "/opt/toolchain/bin/pipx",
  "prek": "/opt/toolchain/bin/prek",
  "python_dir": "/opt/toolchain/bin",
  "python_install_dir": "/opt/python",
  "system_python": "/usr/bin/python3",
  "wheelhouse": "/opt/wheelhouse",
  "npm_cache": "/opt/npm-cache",
  "npm_fixtures": "/opt/npm-fixtures",
  "harness_bin": ["/opt/harness-core/bin", "/opt/harness-full/bin"],
  "runner_python": "/opt/runner/bin/python"
}
EOF
RUN printf '#!/bin/sh\nexec sh /usr/local/bin/deploy-entry %s "$@"\n' versions > /usr/local/bin/versions; \
    printf '#!/bin/sh\nexec sh /usr/local/bin/deploy-entry %s "$@"\n' manifest > /usr/local/bin/manifest; \
    chmod 755 /usr/local/bin/versions /usr/local/bin/manifest
WORKDIR /work

# ------------------------------------------------------------- runner -------
FROM base AS runner
ARG PYTHON_RUNNER
COPY tests/deploy/docker/runner-requirements.txt /tmp/runner-requirements.txt
RUN uv venv -q --python "$PYTHON_RUNNER" /opt/runner; \
    VIRTUAL_ENV=/opt/runner UV_PYTHON_DOWNLOADS=never uv pip install -q --require-hashes \
      -r /tmp/runner-requirements.txt --no-cache

# ------------------------------------------------------- npm fixtures -------
# `npm ci` fills the cache from the lock; the README's install lines, run in a
# repo that holds only vitest, cache the packuments an offline `npm i -D` reads.
FROM base AS npm-fixtures
COPY tests/deploy/docker/npm-fixtures/ /opt/npm-fixtures/
RUN export npm_config_cache=/opt/npm-cache npm_config_update_notifier=false npm_config_fund=false npm_config_audit=false; \
    cd /opt/npm-fixtures; npm ci --ignore-scripts; rm -rf node_modules; \
    vitest=$(node -p "require('./package.json').devDependencies.vitest"); \
    jest=$(node -p "require('./package.json').devDependencies.jest"); \
    mkdir /tmp/vitest-only; cd /tmp/vitest-only; npm init -y >/dev/null; \
    npm i -D --ignore-scripts "vitest@$vitest"; \
    npm i -D --ignore-scripts "@vitest/coverage-v8@${vitest%%.*}"; \
    npm i -D --ignore-scripts @vitest/coverage-v8; \
    mkdir /tmp/jest-only; cd /tmp/jest-only; npm init -y >/dev/null; \
    npm i -D --ignore-scripts "jest@$jest" jest-junit husky; \
    rm -rf /tmp/vitest-only /tmp/jest-only; chown -R 1000:1000 /opt/npm-cache

# -------------------------------------------------------- wheelhouse -------
FROM base AS wheelhouse
ARG TARGETARCH
COPY tools/deploy/lock.py tools/deploy/pins.py tools/deploy/pins.toml tools/deploy/wheelhouse.lock /tmp/lock/
RUN arch=$([ "$TARGETARCH" = arm64 ] && echo aarch64 || echo x86_64); \
    python3.12 /tmp/lock/lock.py fetch --os linux --arch "$arch" --dest /opt/wheelhouse \
      --pins /tmp/lock/pins.toml --lock /tmp/lock/wheelhouse.lock; \
    rm -rf /tmp/lock

# ------------------------------------------------------ harness-core -------
FROM base AS harness-core
ARG CURSOR_AGENT_LINUX_X64_URL
ARG CURSOR_AGENT_LINUX_X64_SHA256
COPY tests/deploy/docker/harness-core/ /opt/harness-core/
RUN cd /opt/harness-core; \
    npm ci --no-audit --no-fund --cache /tmp/npm-cache; rm -rf /tmp/npm-cache; \
    mkdir bin; ln -s ../node_modules/.bin/claude ../node_modules/.bin/codex bin/; \
    for alias in node_modules/claude-code-* node_modules/codex-*; do \
      name=$(basename "$alias"); tool=${name%%-[0-9]*}; \
      case "$tool" in claude-code) target=bin/claude.exe;; codex) target=bin/codex.js;; esac; \
      ln -s "../$alias/$target" "bin/$(echo "$tool" | sed 's/-code//')-${name##*-}"; \
    done
RUN fetch "$CURSOR_AGENT_LINUX_X64_URL" "$CURSOR_AGENT_LINUX_X64_SHA256" /tmp/cursor.tar.gz; \
    mkdir -p /opt/cursor-agent; tar -xzf /tmp/cursor.tar.gz -C /opt/cursor-agent; rm /tmp/cursor.tar.gz; \
    ln -s /opt/cursor-agent/dist-package/cursor-agent /opt/harness-core/bin/cursor-agent

# ------------------------------------------------------ harness-full -------
FROM base AS harness-full
ARG JUNIE_LINUX_X64_URL
ARG JUNIE_LINUX_X64_SHA256
ARG GOOSE_LINUX_X64_URL
ARG GOOSE_LINUX_X64_SHA256
ARG BUN_LINUX_X64_URL
ARG BUN_LINUX_X64_SHA256
COPY tests/deploy/docker/harness-full/ /opt/harness-full/
COPY tests/deploy/docker/aider-requirements.txt tests/deploy/docker/agent-sdk-requirements.txt /tmp/req/
# Junie's postinstall downloads its release into $HOME. Unpacking the pinned,
# hash-checked zip there first makes the postinstall find it and skip the fetch.
RUN version=$(node -p "require('/opt/harness-full/package-lock.json').packages['node_modules/@jetbrains/junie-cli'].version.split('.').slice(0,2).join('.')"); \
    home=/opt/junie-home/.local/share/junie; mkdir -p "$home/versions/$version"; \
    fetch "$JUNIE_LINUX_X64_URL" "$JUNIE_LINUX_X64_SHA256" /tmp/junie.zip; \
    unzip -q /tmp/junie.zip -d "$home/versions/$version"; rm /tmp/junie.zip; \
    chmod -R a+rX,u+w /opt/junie-home; chmod -R a+x "$home/versions/$version"; \
    ln -s "$home/versions/$version" "$home/current"
RUN fetch "$BUN_LINUX_X64_URL" "$BUN_LINUX_X64_SHA256" /tmp/bun.zip; \
    unzip -q /tmp/bun.zip -d /tmp/bun; install -D -m 755 /tmp/bun/*/bun /opt/bun/bin/bun; rm -rf /tmp/bun*; \
    ln -s bun /opt/bun/bin/bunx
# Other postinstalls also write under that HOME (bun, node-gyp and opencode
# caches); only Junie's files stay.
RUN cd /opt/harness-full; export PATH=/opt/bun/bin:$PATH; \
    HOME=/opt/junie-home npm ci --no-audit --no-fund --cache /tmp/npm-cache; rm -rf /tmp/npm-cache; \
    cd /opt/junie-home; rm -rf .bun .cache .config .local/state .local/share/opencode
# npm links no .bin/cline: cline and its platform package both claim the name.
# Junie's shim reads its versions from $JUNIE_DATA, else from $HOME, which a
# sandbox replaces, so the wrapper names the pinned install.
RUN cd /opt/harness-full; mkdir bin; \
    for tool in gemini opencode copilot cn crush amp omp; do ln -s "../node_modules/.bin/$tool" bin/; done; \
    ln -s ../node_modules/cline/bin/cline bin/cline; ln -s /opt/bun/bin/bun bin/bun; \
    printf '#!/bin/sh\nexport JUNIE_DATA="${JUNIE_DATA:-/opt/junie-home/.local/share/junie}"\nexec /opt/junie-home/.local/bin/junie "$@"\n' \
      > bin/junie; chmod 755 bin/junie
RUN fetch "$GOOSE_LINUX_X64_URL" "$GOOSE_LINUX_X64_SHA256" /tmp/goose.tar.bz2; \
    mkdir -p /opt/goose; tar -xjf /tmp/goose.tar.bz2 -C /opt/goose; rm /tmp/goose.tar.bz2; \
    ln -s "$(find /opt/goose -type f -name goose | head -1)" /opt/harness-full/bin/goose
RUN uv venv -q --python 3.12 /opt/aider; \
    VIRTUAL_ENV=/opt/aider uv pip install -q --require-hashes --no-cache -r /tmp/req/aider-requirements.txt; \
    ln -s /opt/aider/bin/aider /opt/harness-full/bin/aider; \
    uv venv -q --python 3.12 /opt/agent-sdk-py; \
    VIRTUAL_ENV=/opt/agent-sdk-py uv pip install -q --require-hashes --no-cache -r /tmp/req/agent-sdk-requirements.txt; \
    rm -rf /tmp/req

# ------------------------------------------------------------- act ----------
FROM base AS act-tools
ARG ACT_LINUX_X64_URL
ARG ACT_LINUX_X64_SHA256
ARG ACTIONS
RUN fetch "$ACT_LINUX_X64_URL" "$ACT_LINUX_X64_SHA256" /tmp/act.tar.gz; \
    mkdir -p /opt/act/bin; tar -xzf /tmp/act.tar.gz -C /opt/act/bin act; rm /tmp/act.tar.gz; \
    for pinned in $ACTIONS; do \
      repo=${pinned%@*}; sha=${pinned#*@}; dir="/opt/act/cache/$(echo "$repo" | tr / -)@$sha"; \
      git init -q "$dir"; git -C "$dir" fetch -q --depth 1 "https://github.com/$repo" "$sha"; \
      git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD; \
    done; \
    runner=$(uv python find 3.12); version=$("$runner" -c 'import platform; print(platform.python_version())'); \
    mkdir -p "/opt/hostedtoolcache/Python/$version"; \
    cp -a "$(dirname "$(dirname "$runner")")" "/opt/hostedtoolcache/Python/$version/x64"; \
    touch "/opt/hostedtoolcache/Python/$version/x64.complete"

# ------------------------------------------------------------- gui ----------
FROM base AS gui-tools
ARG VSCODE_LINUX_X64_URL
ARG VSCODE_LINUX_X64_SHA256
ARG ZED_LINUX_X64_URL
ARG ZED_LINUX_X64_SHA256
RUN fetch "$VSCODE_LINUX_X64_URL" "$VSCODE_LINUX_X64_SHA256" /tmp/vscode.tar.gz; \
    mkdir -p /opt/vscode; tar -xzf /tmp/vscode.tar.gz -C /opt/vscode --strip-components 1; rm /tmp/vscode.tar.gz; \
    fetch "$ZED_LINUX_X64_URL" "$ZED_LINUX_X64_SHA256" /tmp/zed.tar.gz; \
    mkdir -p /opt/zed; tar -xzf /tmp/zed.tar.gz -C /opt/zed --strip-components 1; rm /tmp/zed.tar.gz

# ------------------------------------------------------------ targets -------
FROM base AS cells-pre
COPY --from=runner /opt/runner /opt/runner
COPY --from=npm-fixtures --chown=1000:1000 /opt/npm-cache /opt/npm-cache
COPY --from=npm-fixtures /opt/npm-fixtures /opt/npm-fixtures
USER 1000:1000

FROM cells-pre AS cells
COPY --chmod=755 tests/deploy/docker/entry.sh /usr/local/bin/deploy-entry
COPY --from=wheelhouse /opt/wheelhouse /opt/wheelhouse

FROM cells-pre AS core-pre
COPY --from=harness-core /opt/harness-core /opt/harness-core
COPY --from=harness-core /opt/cursor-agent /opt/cursor-agent

FROM core-pre AS core
COPY --chmod=755 tests/deploy/docker/entry.sh /usr/local/bin/deploy-entry
COPY --from=wheelhouse /opt/wheelhouse /opt/wheelhouse

FROM core-pre AS full-pre
COPY --from=harness-full /opt/harness-full /opt/harness-full
COPY --from=harness-full /opt/junie-home /opt/junie-home
COPY --from=harness-full /opt/bun /opt/bun
COPY --from=harness-full /opt/goose /opt/goose
COPY --from=harness-full /opt/aider /opt/aider
COPY --from=harness-full /opt/agent-sdk-py /opt/agent-sdk-py

FROM full-pre AS full
COPY --chmod=755 tests/deploy/docker/entry.sh /usr/local/bin/deploy-entry
COPY --from=wheelhouse /opt/wheelhouse /opt/wheelhouse

FROM cells-pre AS ci-pre
COPY --from=act-tools /opt/act /opt/act
COPY --from=act-tools /opt/hostedtoolcache /opt/hostedtoolcache

FROM ci-pre AS ci
COPY --chmod=755 tests/deploy/docker/entry.sh /usr/local/bin/deploy-entry
COPY --from=wheelhouse /opt/wheelhouse /opt/wheelhouse

FROM full-pre AS gui-pre
USER root
RUN apt-get update; \
    apt-get install -y --no-install-recommends xvfb xauth dbus-x11 libasound2t64 libatk-bridge2.0-0t64 \
      libgbm1 libgtk-3-0t64 libnss3 libsecret-1-0 libxkbfile1 libxss1 libvulkan1 mesa-vulkan-drivers \
      libxkbcommon-x11-0 libwayland-client0 fontconfig; \
    rm -rf /var/lib/apt/lists/*
COPY --from=gui-tools /opt/vscode /opt/vscode
COPY --from=gui-tools /opt/zed /opt/zed
# VS Code's `code` script refuses to start when /proc/version names Microsoft,
# which it does under Docker Desktop's WSL2 kernel and not on a CI runner.
ENV DONT_PROMPT_WSL_INSTALL=1
USER 1000:1000

FROM gui-pre AS gui
COPY --chmod=755 tests/deploy/docker/entry.sh /usr/local/bin/deploy-entry
COPY --from=wheelhouse /opt/wheelhouse /opt/wheelhouse
