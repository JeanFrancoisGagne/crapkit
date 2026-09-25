function Straight($a) {
    $b = $a + 1
    return $b
}

function FourDeep($a, $b, $c, $d) {
    if ($a) {
        if ($b) {
            if ($c) {
                if ($d) {
                    return 1
                }
            }
        }
    }
    return 0
}

function NestedLoops($n) {
    $t = 0
    foreach ($i in $n) {
        foreach ($j in $n) {
            foreach ($k in $n) {
                $t++
            }
        }
    }
    return $t
}

function FlatSeven($a) {
    $n = 0
    if ($a -eq 1) {
        $n++
    }
    if ($a -eq 2) {
        $n++
    }
    if ($a -eq 3) {
        $n++
    }
    if ($a -eq 4) {
        $n++
    }
    if ($a -eq 5) {
        $n++
    }
    if ($a -eq 6) {
        $n++
    }
    if ($a -eq 7) {
        $n++
    }
    return $n
}
