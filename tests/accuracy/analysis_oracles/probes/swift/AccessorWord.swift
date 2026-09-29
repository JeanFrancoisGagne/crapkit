// A method that calls get() on a result, then another method.
struct Loader {
    func load(r: Outcome) -> Int {
        return r.get()
    }

    func reset(flag: Bool) {
        if flag {
            show(1)
        }
    }
}
