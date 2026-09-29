// An initializer that delegates to super.init, then a method.
class Child: Base {
    init(id: Int, q: Int) {
        super.init(id: id,
                   q: q)
    }

    override func reset() {
        if flag {
            super.reset()
        }
    }
}
