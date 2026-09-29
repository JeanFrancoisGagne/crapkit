class Anonymous {
    Object make() {
        return new Object() {
            private int d;
            int read() {
                return 1;
            }
        };
    }
}
