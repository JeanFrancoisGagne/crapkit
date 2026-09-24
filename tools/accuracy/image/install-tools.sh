#!/bin/sh
# Installs the accuracy oracles tools/accuracy/pins.toml names, one tool per
# argument:
#
#     install-tools.sh PINS TOOL...
#
# Every download is checked against the sha256 its pin names before anything
# is unpacked; a mismatch stops the build naming the tool. Programs land in
# /opt/<tool> with a command in /opt/accuracy/bin, which the image puts first
# on PATH. The Python packages and the node oracles are not installed here:
# the Dockerfile runs uv and npm ci over the hashed lock files.
set -eu

PINS=$1
shift
# ACCURACY_BIN and ACCURACY_DOWNLOADS move both directories, for the kit's own
# test of this script (tests/accuracy/kit/test_kit_image.py).
BIN=${ACCURACY_BIN:-/opt/accuracy/bin}
# The Dockerfile mounts a build cache here, so a rebuild reuses a download
# whose sha256 still matches; without the mount the downloads are removed.
DL=${ACCURACY_DOWNLOADS:-/var/cache/accuracy-downloads}
mkdir -p "$BIN" "$DL"

# pin NAME FIELD: a string field of [oracle.NAME] or [oracle."NAME"].
pin() {
    awk -v want="$1" -v field="$2" '
        /^\[/ { inside = ($0 == "[oracle." want "]" || $0 == "[oracle.\"" want "\"]") }
        inside && $1 == field && $2 == "=" {
            value = $0; sub(/^[^=]*= *"/, "", value); sub(/" *$/, "", value); print value; exit
        }' "$PINS"
}

# fetch NAME: download the pinned url, check its sha256, print the local path.
fetch() {
    url=$(pin "$1" url)
    sum=$(pin "$1" sha256)
    [ -n "$url" ] && [ -n "$sum" ] || { echo "install-tools: no pin named $1" >&2; exit 1; }
    file="$DL/$1-$(basename "$url" | tr -c 'A-Za-z0-9._\n-' '_')"
    if [ -f "$file" ] && echo "$sum  $file" | sha256sum --check --status; then
        echo "$file"
        return
    fi
    curl --fail --location --silent --show-error --retry 3 --output "$file" "$url"
    if ! echo "$sum  $file" | sha256sum --check --status; then
        echo "install-tools: $1 from $url does not match the sha256 in pins.toml" >&2
        exit 1
    fi
    echo "$file"
}

# wrap NAME COMMAND...: a command in $BIN that runs COMMAND with its arguments.
wrap() {
    name=$1
    shift
    printf '#!/bin/sh\nexec %s "$@"\n' "$*" > "$BIN/$name"
    chmod 755 "$BIN/$name"
}

unpack() {  # unpack ARCHIVE DEST [tar options]: a tarball stripped of its top directory
    archive=$1 dest=$2
    shift 2
    mkdir -p "$dest"
    tar -xf "$archive" -C "$dest" --strip-components=1 "$@"
}

cpython() {  # cpython-3.X: python-build-standalone into /opt/python/3.X
    minor=${1#cpython-}
    unpack "$(fetch "$1")" "/opt/python/$minor"
    ln -sf "/opt/python/$minor/bin/python$minor" "$BIN/python$minor"
}

install_one() {
    case $1 in
        cpython-*) cpython "$1" ;;
        uv) tar -xzf "$(fetch uv)" -C "$BIN" --strip-components=1 ;;
        node) unpack "$(fetch node)" /opt/node && ln -sf /opt/node/bin/* "$BIN/" ;;
        go) unpack "$(fetch go)" /opt/go && ln -sf /opt/go/bin/go "$BIN/go" ;;
        gocyclo|gocognit) go_tool "$1" ;;
        revive|rust-code-analysis-cli|cargo-crap) one_binary "$1" ;;
        temurin-jre) unpack "$(fetch temurin-jre)" /opt/java && ln -sf /opt/java/bin/java "$BIN/java" ;;
        checkstyle) jar checkstyle /opt/checkstyle checkstyle.jar ;;
        code-maat) jar code-maat /opt/code-maat "$(basename "$(pin code-maat path)")" ;;
        pmd) pmd ;;
        jacoco) jacoco ;;
        bugspots) gem install --local --no-document "$(fetch bugspots)" ;;
        pwsh) unpack_flat "$(fetch pwsh)" /opt/pwsh && ln -sf /opt/pwsh/pwsh "$BIN/pwsh" ;;
        PSComplexity) ps_module PSComplexity ;;
        clang-tidy) clang_tidy ;;
        oclint) oclint ;;
        swiftlint) unzip -o -q "$(fetch swiftlint)" -d /opt/swiftlint && ln -sf /opt/swiftlint/swiftlint "$BIN/swiftlint" ;;
        shellmetrics) install -m 755 "$(fetch shellmetrics)" "$(pin shellmetrics path)" ;;
        *) echo "install-tools: no installer for $1" >&2; exit 1 ;;
    esac
}

unpack_flat() {  # unpack_flat ARCHIVE DEST: a tarball with no top directory
    mkdir -p "$2"
    tar -xzf "$1" -C "$2"
    chmod 755 "$2"/pwsh 2>/dev/null || true
}

one_binary() {  # one_binary NAME: the file called NAME inside the pinned tarball
    mkdir -p "/tmp/$1.d"
    tar -xzf "$(fetch "$1")" -C "/tmp/$1.d"
    find "/tmp/$1.d" -type f -name "$1" -exec install -m 755 {} "$BIN/$1" \;
    [ -x "$BIN/$1" ] || { echo "install-tools: no file named $1 in its tarball" >&2; exit 1; }
}

go_tool() {  # the module zip is checked here; go install checks it again against sum.golang.org
    fetch "$1" > /dev/null
    GOBIN=$BIN GOPATH=/tmp/gopath GOCACHE=/tmp/gocache GOFLAGS=-modcacherw go install "$(pin "$1" module)"
    rm -rf /tmp/gopath /tmp/gocache
}

jar() {  # jar NAME DIR FILE: a runnable jar and a command that runs it
    mkdir -p "$2"
    cp "$(fetch "$1")" "$2/$3"
    wrap "$1" java -jar "$2/$3"
}

pmd() {
    unzip -o -q "$(fetch pmd)" -d /opt
    mv "/opt/pmd-bin-$(pin pmd version)" /opt/pmd
    ln -sf /opt/pmd/bin/pmd "$BIN/pmd"
}

jacoco() {
    unzip -o -q "$(fetch jacoco)" -d /opt/jacoco
    wrap jacococli java -jar /opt/jacoco/lib/jacococli.jar
}

ps_module() {  # a PowerShell Gallery .nupkg is a zip of the module plus package metadata
    dest="/opt/pwsh-modules/$1/$(pin "$1" version)"
    mkdir -p "$dest"
    unzip -o -q "$(fetch "$1")" -d "$dest"
    rm -rf "$dest/_rels" "$dest/package" "$dest/[Content_Types].xml" "$dest/$1.nuspec"
}

clang_tidy() {  # only clang-tidy, its runner, its builtin headers and the shared libraries it loads
    mkdir -p /opt/llvm
    archive=$(fetch clang-tidy)
    # The release is compressed with a 1 GiB window, which tar's own --zstd
    # refuses; a pattern that matches nothing (a library linked statically)
    # is not an error, and the version call below is the check.
    zstd -dc --long=31 "$archive" | tar -xf - -C /opt/llvm --strip-components=1 --wildcards \
        '*/bin/clang-tidy*' '*/bin/run-clang-tidy*' '*/lib/clang/*' '*/lib/libLLVM*' \
        '*/lib/libclang-cpp*' || true
    ln -sf /opt/llvm/bin/clang-tidy /opt/llvm/bin/run-clang-tidy "$BIN/"
    "$BIN/clang-tidy" --version > /dev/null
}

oclint() {  # OCLint publishes no Linux binary for 26.02: build it against LLVM 21, as its CI does
    unpack "$(fetch oclint)" /opt/oclint-src
    (cd /opt/oclint-src/oclint-scripts \
        && ./build -release -no-ninja -j "$(nproc)" -llvm-root=/usr/lib/llvm-21 \
        && ./bundle -release -llvm-root=/usr/lib/llvm-21)
    mv /opt/oclint-src/build/oclint-release /opt/oclint
    mkdir -p /opt/oclint/runtime
    # Every shared library it loads travels with it (LLVM, clang, z3, libxml2
    # and the rest), so the final image needs no LLVM 21 packages. The C
    # library itself stays the final image's own.
    for binary in /opt/oclint/bin/* /opt/oclint/lib/oclint/*/*.so; do
        ldd "$binary" 2>/dev/null | awk '$3 ~ /^\// { print $3 }'
    done | sort -u | grep -Ev '/(libc|libm|libdl|libpthread|librt|ld-linux[^/]*)\.so' \
        | xargs -r cp -L -t /opt/oclint/runtime
    printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/oclint/runtime exec /opt/oclint/bin/oclint "$@"\n' \
        > /opt/oclint/oclint-command
    chmod 755 /opt/oclint/oclint-command
    rm -rf /opt/oclint-src
}

for tool in "$@"; do
    echo "install-tools: $tool $(pin "$tool" version)"
    install_one "$tool"
done
[ -n "${KEEP_DOWNLOADS:-}" ] || rm -rf "$DL"
