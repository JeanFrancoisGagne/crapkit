// Swift: a guard, a switch with a where clause, and a closure.
struct Token {
    let kind: String
    let text: String
}

func classify(_ token: Token?) -> Int {
    guard let token = token else {
        return -1
    }
    switch token.kind {
    case "number" where token.text.hasPrefix("-"):
        return 2
    case "number":
        return 1
    case "word", "name":
        return 3
    default:
        return 0
    }
}

func longWords(_ words: [String], min: Int) -> [String] {
    return words.filter { $0.count >= min && !$0.isEmpty }
}
