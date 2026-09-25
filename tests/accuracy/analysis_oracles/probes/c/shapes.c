int sibling_ifs(int a, int b, int c) {
    if (a > 0) {
        return a;
    }
    if (b) {
        g();
        if (c) {
            return 1;
        }
    }
    return 0;
}
