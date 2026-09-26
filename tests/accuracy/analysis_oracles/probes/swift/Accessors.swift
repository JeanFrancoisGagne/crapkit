// A computed property with a getter and a setter, and a subscript.
struct Box {
    var stored = 0

    var value: Int {
        get {
            return stored
        }
        set {
            stored = newValue
        }
    }

    subscript(index: Int) -> Int {
        return stored + index
    }
}
