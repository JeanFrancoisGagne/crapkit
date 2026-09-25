pub fn branches(a: i32) i32 {
    if (a > 2) {
        return 3;
    } else if (a > 1) {
        return 2;
    } else {
        return 1;
    }
}

pub fn pick(k: i32) i32 {
    switch (k) {
        1 => return 10,
        2 => return 20,
        3 => return 30,
        else => return 0,
    }
}

pub fn logic(a: bool, b: bool, c: bool, d: bool) i32 {
    if (a and b and c or d) {
        return 1;
    }
    return 0;
}

pub fn loops(n: i32) i32 {
    var i: i32 = 0;
    while (i < n) {
        i += 1;
    }
    while (i > 0) {
        i -= 1;
    }
    return i;
}

pub fn outer(n: usize) usize {
    outer: for (0..n) |i| {
        for (0..n) |j| {
            if (i * j > n) {
                break :outer;
            }
        }
    }
    return 0;
}

pub fn fact(n: u64) u64 {
    if (n <= 1) {
        return 1;
    }
    return n * fact(n - 1);
}
