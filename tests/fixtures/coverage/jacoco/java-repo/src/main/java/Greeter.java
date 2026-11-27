/** A class in the default package: JaCoCo names its sourcefile alone. */
public class Greeter {
    public static String greet(String name) {
        if (name.isEmpty()) {
            return "hello";
        }
        return "hello " + name;
    }
}
