func straight(a: Int) -> Int {
    let b = a + 1
    return b
}

func fourDeep(a: Bool, b: Bool, c: Bool, d: Bool) -> Int {
    if a {
        if b {
            if c {
                if d {
                    return 1
                }
            }
        }
    }
    return 0
}

func nestedLoops(n: Int) -> Int {
    var t = 0
    for _ in 0..<n {
        for _ in 0..<n {
            for _ in 0..<n {
                t += 1
            }
        }
    }
    return t
}

func flatSeven(a: Int) -> Int {
    var n = 0
    if a == 1 {
        n += 1
    }
    if a == 2 {
        n += 1
    }
    if a == 3 {
        n += 1
    }
    if a == 4 {
        n += 1
    }
    if a == 5 {
        n += 1
    }
    if a == 6 {
        n += 1
    }
    if a == 7 {
        n += 1
    }
    return n
}
