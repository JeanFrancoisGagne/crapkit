pub fn branches(a: i32) -> i32 {
    if a > 2 {
        3
    } else if a > 1 {
        2
    } else {
        1
    }
}

pub fn pick(k: i32) -> i32 {
    match k {
        1 => 10,
        2 => 20,
        3 => 30,
        _ => 0,
    }
}

pub fn logic(a: bool, b: bool, c: bool, d: bool) -> i32 {
    if a && b && c || d {
        return 1;
    }
    0
}

pub fn loops(n: i32) -> i32 {
    let mut i = 0;
    while i < n {
        i += 1;
    }
    while i > 0 {
        i -= 1;
    }
    i
}

pub fn outer(n: i32) -> i32 {
    'outer: for i in 0..n {
        for j in 0..n {
            if i * j > n {
                break 'outer;
            }
        }
    }
    0
}

pub fn fact(n: u64) -> u64 {
    if n <= 1 {
        return 1;
    }
    n * fact(n - 1)
}
