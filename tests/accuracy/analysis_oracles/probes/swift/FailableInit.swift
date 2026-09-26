// A failable initializer, then a method.
struct Code {
    let raw: Int

    init?(raw: Int) {
        if raw < 0 {
            return nil
        }
        self.raw = raw
    }

    func shown() -> String {
        return "\(raw)"
    }
}
