// Objective-C methods with a guard and a loop.
#import <Foundation/Foundation.h>

@interface Tally : NSObject
- (NSInteger)sum:(NSArray<NSNumber *> *)values skipNegative:(BOOL)skip;
@end

@implementation Tally
- (NSInteger)sum:(NSArray<NSNumber *> *)values skipNegative:(BOOL)skip
{
    NSInteger total = 0;
    for (NSNumber *value in values) {
        if (skip && value.integerValue < 0) {
            continue;
        }
        total += value.integerValue;
    }
    return total;
}
@end
