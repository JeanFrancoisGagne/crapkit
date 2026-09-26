// Shapes crapkit reads wrong, found by the Objective-C differentials
// (test_objc_oracles); each probe is a strict xfail until its defect is fixed.
#define API_AVAILABLE(...)
int use(int k);

__attribute__((objc_root_class))
@interface Defects
@end

int elvis(int a, int b) {
    return a ?: b;
}

@implementation Defects

- (int)run:(int)a
      with:(int)b API_AVAILABLE(ios(10)) {
    if (a) {
        return b;
    }
    return 0;
}

- (int)drain:(int)n {
    @autoreleasepool {
        for (int i = 0; i < n; i++) {
            if (i == 2) {
                return i;
            }
        }
    }
    return 0;
}

- (void)finish:(void (^)(int))done {
    done(1);
}

@end
