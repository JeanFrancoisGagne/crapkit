// Overloads: functions that share a name and differ in their parameters
// (ISO/IEC 14882:2020 [over.load]). A call is recursion only when it reaches
// the function itself (Sonar Cognitive Complexity v1.7, Recursion).
struct View {
    const char* data;

    bool starts_with(View prefix) const {
        return prefix.data == data;
    }

    // Forwards to the View overload, with as many arguments.
    bool starts_with(const char* prefix) const {
        return starts_with(View{prefix});
    }
};

// Forwards to the longer overload.
int depth(int n) {
    return depth(n, 0);
}

// Calls itself with two arguments, which no other overload takes.
int depth(int n, int acc) {
    if (n == 0) {
        return acc;
    }
    return depth(n - 1, acc + 1);
}
