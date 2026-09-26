// Objective-C++ methods that compile without Apple's SDK, so OCLint and
// clang-tidy read them in test_objc_oracles. Only statements tree-sitter-objc
// also parses, since the differential lists functions through it.
__attribute__((objc_root_class))
@interface Mixed
@end

@implementation Mixed

- (int)countdown:(int)a {
    while (a > 0) {
        if (a == 3) {
            return 1;
        }
        a--;
    }
    return 0;
}

- (int)deref:(int *)p {
    if (p == nullptr) {
        return 0;
    }
    return *p;
}

- (void)after:(void (^)(int))done {
    for (int i = 0; i < 2; i++) {
        done(i);
    }
}

@end
