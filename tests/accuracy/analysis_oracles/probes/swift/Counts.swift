// An argument label spelled like a keyword, with its own parameter name.
func keywordLabel(for name: String) -> String {
    return name
}

// An optional function-typed parameter: a type, no decision.
func optionalMark(done: (() -> Void)? = nil) {
    show(1)
}

// A tuple-typed parameter: one parameter.
func tupleParam(pair: (Int, Int)) -> Int {
    return pair.0
}

// Two try expressions: no structure, no nesting.
func tryTwice() throws {
    try first()
    try second()
}

// A guard: its else body sits one level deep.
func guardAlone(ok: Bool) {
    guard ok else {
        return
    }
    show(1)
}

// A guard inside an if.
func guardInIf(a: Bool, b: Bool) {
    if a {
        guard b else {
            return
        }
        show(1)
    }
}

// A compiler directive whose condition holds &&: no runtime decision.
func directiveAnd() {
    #if canImport(Darwin) && !canImport(FoundationNetworking)
    show(1)
    #else
    show(2)
    #endif
}

// A comment right inside an else.
func elseComment(a: Bool) {
    if a {
        show(1)
    } else {
        // the other way
        show(2)
    }
}
