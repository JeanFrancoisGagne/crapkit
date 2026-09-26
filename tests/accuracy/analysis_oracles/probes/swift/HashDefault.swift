// A plain function, then a #fileID default value on the signature's line.
func plain() -> Int {
    return 1
}

func logged(file: String = #fileID) -> String {
    return file
}
