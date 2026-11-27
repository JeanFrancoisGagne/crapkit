// The shapes the lcov ground truth works by hand; drive.rs calls them.
pub fn if_else(flag: bool) -> i32 {
    if flag {
        return 1;
    }
    2
}

pub fn match_arm(c: char) -> i32 {
    match c {
        'a' => 1,
        'b' => 2,
        _ => 0,
    }
}

pub fn looped(items: &[i32]) -> i32 {
    let mut total = 0;
    for item in items {
        total += item;
    }
    total
}

pub fn branchless(x: i32) -> i32 {
    let y = x * 2;
    y + 1
}

pub fn one_line(x: i32) -> i32 { x + 1 }

pub fn untouched(x: i32) -> i32 {
    if x > 0 {
        return x;
    }
    -x
}

pub struct Counter {
    pub value: i32,
}

impl Counter {
    pub fn bump(&mut self, by: i32) -> i32 {
        if by > 0 {
            self.value += by;
        }
        self.value
    }
}
