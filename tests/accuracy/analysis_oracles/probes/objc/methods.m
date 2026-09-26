// Objective-C methods and C functions that compile without Apple's SDK (a root
// class, blocks, Objective-C exceptions), so OCLint and clang-tidy read them in
// test_objc_oracles.
int use(int k);

__attribute__((objc_root_class))
@interface Probe
@end

@implementation Probe

- (int)pick:(int)a with:(int)b {
    if (a > 0 && b > 0) {
        return 1;
    } else if (a < 0 || b < 0) {
        return 2;
    }
    return a ? 3 : 4;
}

- (int)scan:(id)items {
    for (id item in items) {
        if (item) {
            return 1;
        }
    }
    return 0;
}

- (int)recover:(id)x {
    @try {
        use(1);
    } @catch (id e) {
        if (e) {
            return 1;
        }
    } @finally {
        use(2);
    }
    return 0;
}

- (int)locked:(id)x {
    @synchronized (self) {
        if (x) {
            return 1;
        }
    }
    return 0;
}

- (int)drained:(int)n {
    @autoreleasepool {
        while (n > 0) {
            if (n == 3) {
                return 1;
            }
            n--;
        }
    }
    return 0;
}

- (int)kind:(int)k {
    switch (k) {
        case 1:
            return 10;
        case 2:
            return 20;
        case 3:
            return 30;
        default:
            return 0;
    }
}

- (int)count:(int)n {
    if (n <= 0) {
        return 0;
    }
    return [self count:n - 1];
}

- (void)each:(void (^)(int))visit {
    visit(1);
}

+ (int)twice:(int)a {
    return a > 0 ? a * 2 : 0;
}

@end

static int tally(id items) {
    int t = 0;
    for (id item in items) {
        if (item) {
            t++;
        }
    }
    return t;
}

static int attempt(id x) {
    @try {
        use(1);
    } @catch (id e) {
        return 1;
    }
    return 0;
}

static int held(id x) {
    @synchronized (x) {
        if (x) {
            return 1;
        }
    }
    return 0;
}
