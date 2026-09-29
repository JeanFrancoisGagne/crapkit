branches() {
    if [ "$1" -gt 2 ]; then
        echo 3
    elif [ "$1" -gt 1 ]; then
        echo 2
    else
        echo 1
    fi
}

pick() {
    case "$1" in
        a) echo 10 ;;
        b) echo 20 ;;
        c) echo 30 ;;
        *) echo 0 ;;
    esac
}

logic() {
    if [ -n "$1" ] && [ -n "$2" ] && [ -n "$3" ] || [ -n "$4" ]; then
        return 1
    fi
    return 0
}

loops() {
    i=0
    while [ "$i" -lt "$1" ]; do
        i=$((i + 1))
    done
    until [ "$i" -le 0 ]; do
        i=$((i - 1))
    done
    echo "$i"
}

fact() {
    if [ "$1" -le 1 ]; then
        echo 1
        return
    fi
    echo $(( $1 * $(fact $(( $1 - 1 ))) ))
}
