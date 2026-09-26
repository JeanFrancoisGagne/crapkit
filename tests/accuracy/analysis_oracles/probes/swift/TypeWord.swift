// A method that returns its `type` property, then another method.
struct Trust {
    let type: Int

    func value() -> Int {
        return type
    }

    func check(flag: Bool) {
        if flag {
            show(1)
        }
    }
}
