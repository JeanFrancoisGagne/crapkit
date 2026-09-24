# syntax=docker/dockerfile:1.7
# The accuracy image: every oracle tools/accuracy/pins.toml names, at its pinned
# version, plus Python 3.11 to 3.14 with the hashed nightly lock installed and
# both node oracle directories. The crapkit under test is never baked in: the
# entry point installs the checkout mounted at /src.
#
# Build from the repository root; the context is tools/accuracy:
#
#     docker build -f tools/accuracy/image/Dockerfile \
#         -t ghcr.io/jeanfrancoisgagne/crapkit-accuracy:$(python tools/accuracy/run.py image-tag) \
#         tools/accuracy
#
# Run a tier with no network, as CI does:
#
#     docker run --rm --network none -v "$PWD:/src" <image> \
#         python tools/accuracy/run.py --tier nightly --shard analysis
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
# apt reads snapshot.debian.org at the instant pins.toml names, so a rebuild with
# unchanged pins installs the same packages. Both values equal pins.toml's
# [snapshot] table (tests/accuracy/kit/test_kit_oracles.py checks it). trixie,
# because cargo-crap 0.5.0 and SwiftLint 0.65.1 need a newer glibc than bookworm's.
ARG DEBIAN_SNAPSHOT=20260920T000000Z

# --- OCLint 26.02, built in a stage of its own ---------------------------------
# OCLint publishes no Linux binary for 26.02, so it is built against LLVM 21 from
# apt.llvm.org the way its own CI builds it. apt.llvm.org keeps only its newest
# build: the exact version below makes a rebuild after it moves fail here, named,
# instead of building another OCLint. The key is checked by fingerprint.
FROM ${BASE} AS oclint
ARG DEBIAN_SNAPSHOT
ARG LLVM21_VERSION=1:21.1.8~++20251221033036+2078da43e25a-1~exp1~20251221153213.50
ARG LLVM_KEY_FINGERPRINT=6084F3CF814B57C1CF12EFD515CF4D18AF4F7421
ENV DEBIAN_FRONTEND=noninteractive
RUN rm -f /etc/apt/sources.list.d/debian.sources \
 && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s trixie main\n' \
        "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg cmake make g++ python3 \
        zlib1g-dev libzstd-dev libncurses-dev libxml2-dev libedit-dev \
 && curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/apt-llvm.gpg \
 && gpg --show-keys --with-colons /usr/share/keyrings/apt-llvm.gpg | grep -q "^fpr:::::::::$LLVM_KEY_FINGERPRINT:$" \
 && echo 'deb [signed-by=/usr/share/keyrings/apt-llvm.gpg] http://apt.llvm.org/trixie/ llvm-toolchain-trixie-21 main' \
        > /etc/apt/sources.list.d/apt-llvm.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends llvm-21-dev="$LLVM21_VERSION" \
        libclang-21-dev="$LLVM21_VERSION" clang-21="$LLVM21_VERSION" \
        libclang-cpp21-dev="$LLVM21_VERSION" \
 && rm -rf /var/lib/apt/lists/*
COPY pins.toml image/install-tools.sh /opt/accuracy/
RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh /opt/accuracy/install-tools.sh /opt/accuracy/pins.toml oclint

# --- the image ---------------------------------------------------------------------
FROM ${BASE}
ARG DEBIAN_SNAPSHOT
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    CRAPKIT_ACCURACY_NODE_ROOT=/opt/accuracy/node \
    CRAPKIT_ACCURACY_IN_IMAGE=1 \
    PSModulePath=/opt/pwsh-modules \
    PATH=/opt/accuracy/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# git for crapkit and the history oracles; ruby with rugged and rainbow for
# bugspots; faketime for bugspots at a fixture's HEAD date; the ICU, Kerberos
# and TLS libraries pwsh loads (SwiftLint loads curl and libxml2); the C and
# C++ system headers clang-tidy and OCLint parse fmt and cJSON against; zstd,
# xz and unzip for the downloads.
RUN rm -f /etc/apt/sources.list.d/debian.sources \
 && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s trixie main\n' \
        "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
 && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s trixie-security main\n' \
        "$DEBIAN_SNAPSHOT" >> /etc/apt/sources.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl git xz-utils zstd unzip bzip2 \
        ruby ruby-rainbow ruby-rugged faketime libfaketime \
        libicu76 libgssapi-krb5-2 libssl3t64 zlib1g libstdc++6 libcurl4t64 libxml2 \
        libc6-dev libstdc++-14-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/accuracy
COPY pins.toml image/install-tools.sh ./

RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml uv cpython-3.11 cpython-3.12 cpython-3.13 cpython-3.14

# One venv per Python, each holding the hashed nightly lock (a superset of the
# push lock) and the build backend the entry point installs /src with. The
# entry point's editable install writes into the venv, so any uid CI passes may
# write them; the chmod runs in this layer, since a later `chmod -R` copies
# every venv file into a layer of its own (1.8 GB).
COPY requirements-nightly.txt image/requirements-build.txt ./
RUN --mount=type=cache,target=/var/cache/uv UV_CACHE_DIR=/var/cache/uv UV_LINK_MODE=copy \
    sh -c 'for minor in 3.11 3.12 3.13 3.14; do \
        uv venv --quiet "/opt/venv/$minor" --python "/opt/python/$minor/bin/python$minor" \
     && uv pip install --quiet --python "/opt/venv/$minor/bin/python" --require-hashes \
            --no-deps -r requirements-nightly.txt -r requirements-build.txt \
     || exit 1; \
    done \
 && chmod -R a+rwX /opt/venv'

RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml node
COPY node/push/package.json node/push/package-lock.json node/push/
COPY node/nightly/package.json node/nightly/package-lock.json node/nightly/
RUN for tier in push nightly; do \
        npm ci --prefix "node/$tier" --ignore-scripts --no-audit --no-fund || exit 1; \
    done \
 && rm -rf /root/.npm

RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml go gocyclo gocognit revive rust-code-analysis-cli cargo-crap
RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml temurin-jre checkstyle pmd jacoco code-maat
RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml bugspots pwsh PSComplexity shellmetrics swiftlint
RUN --mount=type=cache,target=/var/cache/accuracy-downloads KEEP_DOWNLOADS=1 sh install-tools.sh pins.toml clang-tidy
COPY --from=oclint /opt/oclint /opt/oclint
RUN ln -s /opt/oclint/oclint-command /opt/accuracy/bin/oclint

# The full corpus, baked in when a build names its release asset; tools/accuracy/corpus.py
# builds and publishes it, and native cells restore the same tarball from actions/cache.
ARG CORPUS_URL=
ARG CORPUS_SHA256=
RUN mkdir -p /corpus \
 && if [ -n "$CORPUS_URL" ]; then \
        curl --fail --location --silent --show-error --output /tmp/corpus.tar.gz "$CORPUS_URL" \
     && echo "$CORPUS_SHA256  /tmp/corpus.tar.gz" | sha256sum --check --status \
     && tar -xzf /tmp/corpus.tar.gz -C /corpus && rm /tmp/corpus.tar.gz; \
    fi

COPY image/entry.sh /usr/local/bin/accuracy-entry
# A bind-mounted checkout keeps the host's owner, which git calls dubious under
# any other uid, so every directory is safe for every user.
RUN chmod 755 /usr/local/bin/accuracy-entry \
 && useradd --create-home --uid 1000 accuracy \
 && git config --system --add safe.directory '*'
USER accuracy
WORKDIR /src
ENTRYPOINT ["accuracy-entry"]
CMD ["python", "tools/accuracy/run.py", "--tier", "push"]
