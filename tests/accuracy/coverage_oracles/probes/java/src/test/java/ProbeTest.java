// Drive Shapes for one scenario: `call` makes exactly the calls ground_truth.tsv's
// arms_taken column is worked from; `idle` calls nothing, so Shapes never loads.
import org.junit.Test;

public class ProbeTest {
    @Test
    public void drive() {
        if (!"call".equals(System.getenv("PROBE_SCENARIO"))) {
            return;
        }
        Shapes.ifElse(true);
        Shapes.logical(0, 1);
        Shapes.ternary(1);
        Shapes.loop(0);
        Shapes.branchless(1);
        Shapes.oneLine(1);
        Shapes.withLambda(4);
        Shapes.Box box = new Shapes.Box();
        box.value = 5;
        box.method(2);
    }
}
