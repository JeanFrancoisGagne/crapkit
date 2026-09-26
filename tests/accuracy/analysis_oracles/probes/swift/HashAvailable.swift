// An if whose #available condition shares its line with the brace.
func availableIf() -> Int {
    if #available(macOS 10.15, *) {
        return 1
    }
    return 0
}
