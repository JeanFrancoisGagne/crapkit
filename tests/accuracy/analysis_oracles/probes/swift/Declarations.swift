// A protocol whose requirement takes a parameter named type, then a class with a deinit.
protocol Store {
    func load(type: Int) -> Int
}

final class Holder {
    deinit {
        show(0)
    }

    func read(flag: Bool) -> Int {
        if flag {
            return 1
        }
        return 0
    }
}
