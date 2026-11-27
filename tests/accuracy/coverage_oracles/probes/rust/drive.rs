// Drive shapes.rs for one scenario: `call` makes exactly the calls ground_truth.tsv's
// stmts_run column is worked from; `idle` calls nothing. Every function is named
// here, so each is compiled and its lines are mapped in both scenarios.
#[path = "shapes.rs"]
mod shapes;

fn call() {
    shapes::if_else(true);
    shapes::match_arm('a');
    shapes::looped(&[]);
    shapes::branchless(1);
    shapes::one_line(1);
    let mut counter = shapes::Counter { value: 0 };
    counter.bump(0);
}

fn main() {
    let scenario = std::env::args().nth(1).unwrap_or_default();
    if scenario == "call" {
        call();
    }
    if scenario == "never" {
        shapes::untouched(1);
    }
}
