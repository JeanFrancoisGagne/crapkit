straight() {
    b=$(( $1 + 1 ))
    echo "$b"
}

four_deep() {
    if [ -n "$1" ]; then
        if [ -n "$2" ]; then
            if [ -n "$3" ]; then
                if [ -n "$4" ]; then
                    return 1
                fi
            fi
        fi
    fi
    return 0
}

nested_loops() {
    t=0
    for i in $1; do
        for j in $1; do
            for k in $1; do
                t=$(( t + 1 ))
            done
        done
    done
    echo "$t"
}

flat_seven() {
    n=0
    if [ "$1" = 1 ]; then
        n=1
    fi
    if [ "$1" = 2 ]; then
        n=2
    fi
    if [ "$1" = 3 ]; then
        n=3
    fi
    if [ "$1" = 4 ]; then
        n=4
    fi
    if [ "$1" = 5 ]; then
        n=5
    fi
    if [ "$1" = 6 ]; then
        n=6
    fi
    if [ "$1" = 7 ]; then
        n=7
    fi
    echo "$n"
}
