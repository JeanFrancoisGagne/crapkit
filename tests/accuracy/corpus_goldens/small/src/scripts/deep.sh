#!/bin/sh
# A four-deep if, and the two ways a shell function is declared.

deep() {
    if [ -n "$1" ]; then
        if [ -d "$1" ]; then
            if [ -r "$1" ]; then
                if [ -w "$1" ]; then
                    echo "rw"
                fi
            fi
        fi
    fi
}

check_all() {
    for f in "$@"; do
        [ -f "$f" ] && echo "file $f" || echo "missing $f"
    done
}
