class Annotated {
    @Deprecated
    @InlineMe(replacement = "Annotated.parse(json)", imports = "Annotated")
    public static int inlined(int n) {
        return n;
    }
}
