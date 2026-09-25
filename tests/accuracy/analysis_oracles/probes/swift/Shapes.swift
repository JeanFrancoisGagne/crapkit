// A closure that holds an if.
func closureIf(v: [Int]) {
    v.forEach { x in
        if x > 0 {
            show(x)
        }
    }
}
