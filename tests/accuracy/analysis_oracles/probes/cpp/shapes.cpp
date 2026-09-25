// A lambda that holds an if.
void closure_if(std::vector<int> v) {
    std::for_each(v.begin(), v.end(), [](int x) {
        if (x > 0) {
            show(x);
        }
    });
}

// A template parameter whose default holds a less-than comparison.
template <int N, bool E = (N < 19)>
struct Limit {
    static constexpr int value = N;
};

int after_limit(int a) {
    if (a) {
        return 1;
    }
    return 0;
}
