// A header-only C++ template.
#pragma once

template <typename T>
struct Vec2 {
    T x;
    T y;

    T dominant() const
    {
        if (x > y) {
            return x;
        }
        return y;
    }
};
