// A closure that holds an if.
func closureIf(v: [Int]) {
    v.forEach { x in
        if x > 0 {
            show(x)
        }
    }
}

// An if with a case pattern: one condition.
func ifCase(r: Outcome) {
    if case let .failure(e) = r {
        show(e)
    }
}

// A nil-coalescing pick: one binary decision.
func coalesce(a: Int?) -> Int {
    return a ?? 0
}
