#include "shapes.h"

int clamp(int value, int low, int high) {
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}

int grade(int score) {
    if (score > 90) {
        return 5;
    } else if (score > 80) {
        return 4;
    } else if (score > 70) {
        return 3;
    } else if (score > 60) {
        return 2;
    } else if (score > 50) {
        return 1;
    } else if (score < 0) {
        return -1;
    }
    return 0;
}
