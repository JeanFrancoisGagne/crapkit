/* A header with an inline function. */
#ifndef CORPUS_UTIL_H
#define CORPUS_UTIL_H

static inline int clamp_int(int value, int low, int high)
{
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}

#endif
