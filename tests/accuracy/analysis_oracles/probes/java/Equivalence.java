public class Equivalence {
    static int straight(int a) {
        int b = a + 1;
        return b;
    }

    static int fourDeep(boolean a, boolean b, boolean c, boolean d) {
        if (a) {
            if (b) {
                if (c) {
                    if (d) {
                        return 1;
                    }
                }
            }
        }
        return 0;
    }

    static int nestedLoops(int n) {
        int t = 0;
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                for (int k = 0; k < n; k++) {
                    t++;
                }
            }
        }
        return t;
    }

    static int flatSeven(int a) {
        int n = 0;
        if (a == 1) {
            n++;
        }
        if (a == 2) {
            n++;
        }
        if (a == 3) {
            n++;
        }
        if (a == 4) {
            n++;
        }
        if (a == 5) {
            n++;
        }
        if (a == 6) {
            n++;
        }
        if (a == 7) {
            n++;
        }
        return n;
    }
}
