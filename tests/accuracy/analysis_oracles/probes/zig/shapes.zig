pub fn pair(n: usize) struct { usize, usize } {
    var low: usize = 0;
    if (n > 1) {
        low = 1;
    }
    return .{ low, n };
}

pub fn sortWith(comptime T: type, items: []T, comptime lessThan: fn (T, T) bool) void {
    _ = items;
    _ = lessThan;
}

pub fn tries() !void {
    try first();
    try second();
    try third();
}

pub fn payloadElse(x: anyerror!u8) u8 {
    if (x) |v| {
        return v;
    } else |err| if (err != error.Boom) return 1;
    return 0;
}

pub fn indexOf(haystack: []const u8) ?usize {
    return find(haystack, 0);
}

pub fn prongContinue(items: []const u8) usize {
    var n: usize = 0;
    for (items) |c| {
        switch (c) {
            0 => continue,
            1...255 => n += 1,
        }
    }
    return n;
}
