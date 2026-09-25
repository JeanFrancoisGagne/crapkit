pub fn straight(a: i32) i32 {
    const b = a + 1;
    return b;
}

pub fn fourDeep(a: bool, b: bool, c: bool, d: bool) i32 {
    if (a) {
        if (b) {
            if (c) {
                if (d) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

pub fn nestedLoops(n: usize) usize {
    var t: usize = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        var j: usize = 0;
        while (j < n) : (j += 1) {
            var k: usize = 0;
            while (k < n) : (k += 1) {
                t += 1;
            }
        }
    }
    return t;
}

pub fn flatSeven(a: i32) i32 {
    var n: i32 = 0;
    if (a == 1) {
        n += 1;
    }
    if (a == 2) {
        n += 1;
    }
    if (a == 3) {
        n += 1;
    }
    if (a == 4) {
        n += 1;
    }
    if (a == 5) {
        n += 1;
    }
    if (a == 6) {
        n += 1;
    }
    if (a == 7) {
        n += 1;
    }
    return n;
}
