pub fn try_op(a: &str) -> Result<i32, E> {
    let n = parse(a)?;
    Ok(n)
}

pub fn let_else(a: Option<i32>) -> i32 {
    let Some(n) = a else {
        return 0;
    };
    n
}

pub fn empty_closure(n: i32) -> Box<dyn Fn() -> i32> {
    Box::new(move || n)
}

pub fn maybe_sized<P: AsRef<str> + ?Sized>(p: &P) -> usize {
    p.as_ref().len()
}

pub fn tuple_param(glob: &str, r: &mut (char, char), c: char) -> bool {
    glob.is_empty()
}

pub fn spin(n: i32) -> i32 {
    let mut i = 0;
    loop {
        if i > n {
            break;
        }
        i += 1;
    }
    i
}

pub fn with_where<E>(v: &str) -> Result<i32, E>
where
    E: Error,
{
    parse(v)
}

pub fn guarded_arm(x: i32, y: bool) -> i32 {
    match x {
        1 if y => 1,
        _ => 0,
    }
}

pub fn nested_let_else(v: &[Option<i32>]) -> i32 {
    for x in v {
        let Some(n) = x else { return 0 };
        return *n;
    }
    0
}

pub fn closure_depth(v: Option<&str>) -> Option<usize> {
    v.map(|s| s.len())
}

pub fn arm_continue(v: Vec<i32>) -> i32 {
    for r in v {
        let d = match r {
            0 => continue,
            _ => r,
        };
        if d > 1 {
            return d;
        }
    }
    0
}

pub trait Builder {
    fn build(&mut self) -> i32;
}

pub fn after_trait(n: i32) -> i32 {
    n + 1
}
