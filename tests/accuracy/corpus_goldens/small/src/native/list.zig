// Zig: a while with a continue expression, an if/else and a switch.
const std = @import("std");

pub fn sumPositive(values: []const i32) i32 {
    var total: i32 = 0;
    var i: usize = 0;
    while (i < values.len) : (i += 1) {
        if (values[i] > 0) {
            total += values[i];
        }
    }
    return total;
}

pub fn grade(score: u8) u8 {
    return switch (score) {
        90...100 => 'A',
        80...89 => 'B',
        70...79 => 'C',
        else => 'F',
    };
}
