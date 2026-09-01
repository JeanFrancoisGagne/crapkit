# crapkit's MCP server over stdio, for a client or a registry that starts a
# server from a Dockerfile instead of from an installed package.
#
#     docker build -t crapkit .
#     docker run -i --rm -v "$PWD:/repo" -w /repo crapkit
#
# `-i` is not decoration: the protocol is JSON-RPC on stdin and stdout, and
# without it the server reads EOF and exits before the client's `initialize`.
FROM python:3.12-slim

# git, because every tool here shells to the crapkit CLI and the CLI reads git:
# the churn walk, the coupling cache and the blobs the gate reads all run it.
# python:3.12-slim ships none.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# The account that serves. The server only reads, and what it reads is a
# checkout the client mounts from outside.
RUN useradd --create-home --uid 1000 crapkit

# Four paths and no more. `pip install .` needs pyproject.toml plus the two
# files its metadata names, README.md and LICENSE, and a build missing either
# fails on a path rather than on anything that says why.
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

USER crapkit

# A bind mount keeps the host's ownership. Under a different uid git calls that
# dubious and refuses every command, which would leave the tools reporting an
# empty history instead of the repo's own.
RUN git config --global --add safe.directory '*'

# Where `-v "$PWD:/repo" -w /repo` lands, and the directory the server serves
# when no --repo is passed. Mounted somewhere else, the tools answer with the
# missing-config result and the session stays alive.
WORKDIR /repo

ENTRYPOINT ["crapkit", "mcp"]
