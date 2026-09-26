/* An array parameter (ISO/IEC 9899:2018 6.7.6.3: one parameter, adjusted to a
   pointer) and structures in braceless bodies. */
int head_c(const int arr[4]) {
    return arr[0];
}

int pick_c(int a, int b) {
    if (a) b = b > 0 ? 1 : 2;
    return b;
}

void walk_c(int n) {
    for (int i = 0; i < n; ++i)
        if (i) {
            show(i);
        }
}
