pub fn straight(a: i32) -> i32 {
    let b = a + 1;
    b
}

pub fn four_deep(a: bool, b: bool, c: bool, d: bool) -> i32 {
    if a {
        if b {
            if c {
                if d {
                    return 1;
                }
            }
        }
    }
    0
}

pub fn nested_loops(n: i32) -> i32 {
    let mut t = 0;
    for _i in 0..n {
        for _j in 0..n {
            for _k in 0..n {
                t += 1;
            }
        }
    }
    t
}

pub fn flat_seven(a: i32) -> i32 {
    let mut n = 0;
    if a == 1 {
        n += 1;
    }
    if a == 2 {
        n += 1;
    }
    if a == 3 {
        n += 1;
    }
    if a == 4 {
        n += 1;
    }
    if a == 5 {
        n += 1;
    }
    if a == 6 {
        n += 1;
    }
    if a == 7 {
        n += 1;
    }
    n
}

pub fn seven_arms(a: i32) -> i32 {
    match a {
        1 => 10,
        2 => 20,
        3 => 30,
        4 => 40,
        5 => 50,
        6 => 60,
        7 => 70,
        _ => 0,
    }
}

pub fn seven_ifs(a: i32) -> i32 {
    if a == 1 {
        10
    } else if a == 2 {
        20
    } else if a == 3 {
        30
    } else if a == 4 {
        40
    } else if a == 5 {
        50
    } else if a == 6 {
        60
    } else if a == 7 {
        70
    } else {
        0
    }
}
