// A lambda that holds an if.
void closure_if(std::vector<int> v) {
    std::for_each(v.begin(), v.end(), [](int x) {
        if (x > 0) {
            show(x);
        }
    });
}
