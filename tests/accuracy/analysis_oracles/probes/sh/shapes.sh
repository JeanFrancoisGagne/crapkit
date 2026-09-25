quoted_subst() {
    [ "$(nvm deactivate >/dev/null 2>&1 && command -v node)" != '' ]
}

heredoc_msg() {
    cat <<EOF
line one
line two
EOF
}
