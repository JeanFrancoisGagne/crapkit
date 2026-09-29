public class Constructs {
    static int branches(int a) {
        if (a > 2) {
            return 3;
        } else if (a > 1) {
            return 2;
        } else {
            return 1;
        }
    }

    static int pick(int k) {
        switch (k) {
            case 1:
                return 10;
            case 2:
                return 20;
            case 3:
                return 30;
            default:
                return 0;
        }
    }

    static int logic(boolean a, boolean b, boolean c, boolean d) {
        if (a && b && c || d) {
            return 1;
        }
        return 0;
    }

    static int choose(int a) {
        return a > 0 ? 1 : 2;
    }

    static int loops(int n) {
        int i = 0;
        while (i < n) {
            i++;
        }
        do {
            i--;
        } while (i > 0);
        return i;
    }

    static int guarded(Runnable r) {
        try {
            r.run();
        } catch (IllegalStateException e) {
            return 1;
        } catch (RuntimeException e) {
            return 2;
        }
        return 0;
    }

    static int outer(int n) {
        outer:
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                if (i * j > n) {
                    break outer;
                }
            }
        }
        return 0;
    }

    static int fact(int n) {
        if (n <= 1) {
            return 1;
        }
        return n * fact(n - 1);
    }
}
