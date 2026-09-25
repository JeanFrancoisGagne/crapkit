class Ternaries {
    static int pick(int a, int b) {
        return a > 0
            ? 1
            : b > 0
                ? 2
                : 3;
    }

    static int tryLoop(int[] items) {
        try {
            for (int i = 0; i < items.length; i++) {
                items[i] = 0;
            }
        } catch (RuntimeException e) {
            return 1;
        }
        return 0;
    }
}
