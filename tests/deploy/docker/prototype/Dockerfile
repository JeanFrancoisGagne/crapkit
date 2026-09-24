# syntax=docker/dockerfile:1.7
# Prototype to measure sizes and build times for the deploy-suite images.
ARG DEBIAN=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
ARG UV=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc
ARG NODE=node:22.23.3-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4

FROM ${UV} AS uv
FROM ${NODE} AS node

FROM ${DEBIAN} AS base
ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python UV_NO_PROGRESS=1 PIP_NO_INPUT=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
RUN uv python install 3.11.16 3.12.14 3.13.15 \
 && for v in 3.11 3.12 3.13; do ln -s "$(uv python find $v)" /usr/local/bin/python$v; done
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN useradd --create-home --uid 1000 tester

FROM base AS wheelhouse
RUN mkdir /wheelhouse && for v in 3.11 3.12 3.13; do       for c in 0.4.0 0.4.4 0.4.15 0.5.1 0.6.0 0.7.6 0.8.0; do         python$v -m pip download -q -d /wheelhouse --only-binary=:all: "crapkit[py]==$c" lizard==1.24.0 || exit 1;       done;       python$v -m pip download -q -d /wheelhouse --only-binary=:all: pytest pytest-cov coverage pytest-xdist pre-commit build || exit 1;     done; du -sh /wheelhouse; ls /wheelhouse | wc -l

FROM base AS harness
RUN npm install -g --no-fund --no-audit \
      @anthropic-ai/claude-code@2.1.281 @openai/codex@0.156.1 @google/gemini-cli@0.61.0 \
      opencode-ai@1.18.32 @github/copilot@1.0.88 cline@3.0.65 @continuedev/cli@1.5.47 \
      @charmland/crush@0.96.1 @anthropic-ai/claude-agent-sdk@0.3.281 @anthropic-ai/mcpb@2.1.2 \
 && npm cache clean --force && du -sh /usr/local/lib/node_modules

FROM base AS cells
COPY --from=wheelhouse /wheelhouse /wheelhouse
USER tester
WORKDIR /home/tester
