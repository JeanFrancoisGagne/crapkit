func branches(a: Int) -> Int {
    if a > 2 {
        return 3
    } else if a > 1 {
        return 2
    } else {
        return 1
    }
}

func pick(k: Int) -> Int {
    switch k {
    case 1:
        return 10
    case 2:
        return 20
    case 3:
        return 30
    default:
        return 0
    }
}

func logic(a: Bool, b: Bool, c: Bool, d: Bool) -> Int {
    if a && b && c || d {
        return 1
    }
    return 0
}

func choose(a: Int) -> Int {
    return a > 0 ? 1 : 2
}

func loops(n: Int) -> Int {
    var i = 0
    while i < n {
        i += 1
    }
    repeat {
        i -= 1
    } while i > 0
    return i
}

func guarded(r: Runner) -> Int {
    do {
        try r.run()
    } catch RunError.first {
        return 1
    } catch {
        return 2
    }
    return 0
}

func outer(n: Int) -> Int {
    outer: for i in 0..<n {
        for j in 0..<n {
            if i * j > n {
                break outer
            }
        }
    }
    return 0
}

func fact(n: Int) -> Int {
    if n <= 1 {
        return 1
    }
    return n * fact(n: n - 1)
}
