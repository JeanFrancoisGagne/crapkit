public class Shapes {
    int localAnnotation(Object o) {
        @SuppressWarnings("unchecked")
        int x = (int) o;
        return x;
    }

    int afterLocalAnnotation(int n) {
        return n;
    }
}
