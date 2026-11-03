// The shapes the lcov ground truth works by hand; drive.cpp calls them.
#include "shapes.h"

int if_else(bool flag) {
    if (flag) {
        return 1;
    }
    return 2;
}

int logical(int a, int b) {
    if (a > 0 && b > 0) {
        return 1;
    }
    return 0;
}

int ternary(int x) {
    return x > 0 ? 1 : -1;
}

int loop(int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        total += i;
    }
    return total;
}

int branchless(int x) {
    int y = x * 2;
    return y + 1;
}

int one_line(int x) { return x + 1; }

int untouched(int x) {
    if (x > 0) {
        return x;
    }
    return -x;
}

int Box::method(int x) {
    if (x > value) {
        return x;
    }
    return value;
}
