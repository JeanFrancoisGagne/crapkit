// A method that passes a `protocol:` argument label, then another method.
struct Socket {
    func open(name: String) -> Socket {
        return Socket(protocol: name)
    }

    func close(flag: Bool) {
        if flag {
            show(1)
        }
    }
}
