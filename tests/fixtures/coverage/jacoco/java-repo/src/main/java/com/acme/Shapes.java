package com.acme;

import java.util.function.IntUnaryOperator;

/** The shapes the JaCoCo fixtures measure; ShapesTest calls some of them. */
public class Shapes {
    public static int ifElse(boolean flag) {
        if (flag) {
            return 1;
        }
        return 2;
    }

    public static int logical(int a, int b) {
        if (a > 0 && b > 0) {
            return 1;
        }
        return 0;
    }

    public static int branchless(int x) {
        int y = x * 2;
        return y + 1;
    }

    public static int twice(int x) {
        IntUnaryOperator doubler = v -> {
            int w = v * 2;
            return w;
        };
        return doubler.applyAsInt(x);
    }

    public static String grade(int score, boolean curved, boolean late) {
        if (curved && score < 100) {
            score += 5;
        }
        if (late || score < 0) {
            score -= 10;
        }
        if (score >= 90) {
            return "A";
        } else if (score >= 80) {
            return "B";
        } else if (score >= 70) {
            return "C";
        }
        return "F";
    }

    /** An inner class: its lines sit in Shapes.java beside the outer class's. */
    public static class Box {
        private final int value;

        public Box(int value) {
            this.value = value;
        }

        public int method(int x) {
            if (x > value) {
                return x;
            }
            return value;
        }
    }
}
