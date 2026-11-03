// The shapes the JaCoCo ground truth works by hand; ProbeTest calls them.
import java.util.function.IntUnaryOperator;

public class Shapes {
    static int ifElse(boolean flag) {
        if (flag) {
            return 1;
        }
        return 2;
    }

    static int logical(int a, int b) {
        if (a > 0 && b > 0) {
            return 1;
        }
        return 0;
    }

    static int ternary(int x) {
        return x > 0 ? 1 : -1;
    }

    static int loop(int n) {
        int total = 0;
        for (int i = 0; i < n; i++) {
            total += i;
        }
        return total;
    }

    static int branchless(int x) {
        int y = x * 2;
        return y + 1;
    }

    static int oneLine(int x) { return x + 1; }

    static int untouched(int x) {
        if (x > 0) {
            return x;
        }
        return -x;
    }

    static int withLambda(int x) {
        IntUnaryOperator half = v -> {
            if (v > 0) {
                return v / 2;
            }
            return 0;
        };
        return half.applyAsInt(x);
    }

    static class Box {
        int value;

        int method(int x) {
            if (x > value) {
                return x;
            }
            return value;
        }
    }
}
