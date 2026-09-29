#!/bin/sh
# The accuracy image's entry point. It picks the interpreter a cell runs
# (CRAPKIT_ACCURACY_PY: 3.11, 3.12, 3.13 or 3.14; default 3.12), installs the
# checkout in the working directory into that interpreter's venv without
# touching the network, then runs the command it was given:
#
#     docker run --rm --network none -v "$PWD:/src" <image> \
#         python tools/accuracy/run.py --tier nightly --shard analysis
#
# The crapkit under test is never baked into the image: each container
# installs the checkout it was handed, editable, like `pip install -e .`. Any
# uid may run it (CI passes the runner's with --user, so files it writes in the
# checkout stay the runner's); a uid with no home gets one under /tmp.
set -eu
minor=${CRAPKIT_ACCURACY_PY:-3.12}
# ACCURACY_VENVS moves the venvs, for tests/accuracy/kit/test_kit_image.py.
venv=${ACCURACY_VENVS:-/opt/venv}/$minor
if [ ! -x "$venv/bin/python" ]; then
    echo "accuracy-entry: this image has no Python $minor; use 3.11, 3.12, 3.13 or 3.14" >&2
    exit 3
fi
if [ ! -w "${HOME:-/}" ] || [ "${HOME:-/}" = / ]; then
    export HOME=/tmp/accuracy-home
    mkdir -p "$HOME"
fi
export VIRTUAL_ENV="$venv" PATH="$venv/bin:$PATH"
if [ -f pyproject.toml ]; then
    uv pip install --quiet --offline --no-deps --no-build-isolation \
        --python "$venv/bin/python" -e .
fi
exec "$@"
