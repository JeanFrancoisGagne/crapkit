// Objective-C methods: parameters, recursion by selector, a block literal, and
// class extensions that hold instance variables.
@implementation Shapes

- (int)pairFor:(int)a to:(int)b {
    return a + b;
}

- (int)narrower:(int)a to:(int)b {
    return [self narrower:a];
}

- (void)viewDidLoad {
    [super viewDidLoad];
}

- (int)sum:(int)a with:(int)b {
    if (b == 0) {
        return a;
    }
    return [self sum:a + 1 with:b - 1];
}

- (int)join:(int)a and:(int)b {
    return a + b;
}

- (void)withBlock:(NSArray *)items {
    [items enumerateObjectsUsingBlock:^(id item, NSUInteger index, BOOL *stop) {
        if (item) {
            use(item);
        }
    }];
}

@end

@interface Extension () {
    int _first;
    int _second;
}
@end

@interface Adopting () <NSCopying> {
    int _phase;
    unsigned long long _offset;
}
@end

@implementation Factory

+ (instancetype)serializer {
    Factory *serializer = [[self alloc] init];
    return serializer;
}

@end

static int pragmas(int n) {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused"
    int k = n;
#pragma clang diagnostic pop
    return k;
}
