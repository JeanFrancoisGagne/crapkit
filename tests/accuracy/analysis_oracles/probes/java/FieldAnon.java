interface FieldAnon {
    FieldAnon FILTER =
        new FieldAnon() {
            @Override
            public int check(int n) {
                return n;
            }
        };

    int check(int n);
}
