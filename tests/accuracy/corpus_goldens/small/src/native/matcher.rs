// Rust: a seven-arm match (lizard #494), an if let and a loop.
pub fn weight(kind: &str) -> u32 {
    match kind {
        "alpha" => 1,
        "beta" => 2,
        "gamma" => 3,
        "delta" => 4,
        "epsilon" => 5,
        "zeta" => 6,
        _ => 0,
    }
}

pub fn first_even(values: &[i64]) -> Option<i64> {
    for value in values {
        if let Some(half) = value.checked_rem(2) {
            if half == 0 {
                return Some(*value);
            }
        }
    }
    None
}
