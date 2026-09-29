// C++ with a range-for, a ternary and a lambda.
#include <vector>

int area_sum(const std::vector<int> &widths, const std::vector<int> &heights)
{
    int total = 0;
    for (size_t i = 0; i < widths.size() && i < heights.size(); ++i) {
        total += widths[i] > 0 ? widths[i] * heights[i] : 0;
    }
    return total;
}

int count_if_positive(const std::vector<int> &values)
{
    int count = 0;
    auto positive = [](int v) { return v > 0; };
    for (int v : values) {
        if (positive(v)) {
            ++count;
        }
    }
    return count;
}
