enum Policy {
    IDENTITY() {
        @Override
        int value(int n) {
            return n;
        }
    };

    abstract int value(int n);
}
