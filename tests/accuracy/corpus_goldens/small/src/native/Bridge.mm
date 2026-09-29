// Objective-C++: an Objective-C method calling C++.
#import <Foundation/Foundation.h>
#include <vector>

static int longest(const std::vector<int> &runs)
{
    int best = 0;
    for (int run : runs) {
        if (run > best) {
            best = run;
        }
    }
    return best;
}

@interface Bridge : NSObject
- (int)longestOf:(NSArray<NSNumber *> *)runs;
@end

@implementation Bridge
- (int)longestOf:(NSArray<NSNumber *> *)runs
{
    std::vector<int> copy;
    for (NSNumber *run in runs) {
        copy.push_back(run.intValue);
    }
    return copy.empty() ? 0 : longest(copy);
}
@end
