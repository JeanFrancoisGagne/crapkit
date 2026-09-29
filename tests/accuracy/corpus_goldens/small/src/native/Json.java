// Java: an enhanced for, a ternary, a catch and a switch.
package corpus;

import java.util.List;

public class Json {
    public static String quote(String text) {
        if (text == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder("\"");
        for (char c : text.toCharArray()) {
            out.append(c == '"' ? "\\\"" : String.valueOf(c));
        }
        return out.append('"').toString();
    }

    public static int parseOr(String text, int fallback) {
        try {
            return Integer.parseInt(text);
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    public static int kind(List<Object> values) {
        switch (values.size()) {
            case 0:
                return 0;
            case 1:
                return 1;
            default:
                return 2;
        }
    }
}
