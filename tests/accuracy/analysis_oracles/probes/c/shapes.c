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

static unsigned char decimal_point(void)
{
#ifdef ENABLE_LOCALES
    struct lconv *lconv = localeconv();
    return (unsigned char) lconv->decimal_point[0];
#else
    return '.';
#endif
}

int for_then_ifs(int n, int a, int b) {
    for (int i = 0; i < n; i++) {
        if (a) {
            if (b) {
                return i;
            }
        }
    }
    return 0;
}

int braceless_then_loop(int a, int n) {
    if (a) return 0;
    for (int i = 0; i < n; i++) {
        if (i) return 1;
    }
    return 2;
}
