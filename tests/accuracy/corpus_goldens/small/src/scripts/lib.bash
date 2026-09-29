#!/usr/bin/env bash
# Bash: the function keyword, a case and a while read loop.

function pick_color {
    case "$1" in
        error) echo red ;;
        warn|warning) echo yellow ;;
        *) echo plain ;;
    esac
}

count_lines() {
    local n=0
    while IFS= read -r line; do
        if [[ -n "$line" && "$line" != \#* ]]; then
            n=$((n + 1))
        fi
    done < "$1"
    echo "$n"
}
