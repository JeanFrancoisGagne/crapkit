class Lambdas {
    void closureIf(java.util.List<Integer> v) {
        v.forEach(x -> {
            if (x > 0) {
                show(x);
            }
        });
    }
}
