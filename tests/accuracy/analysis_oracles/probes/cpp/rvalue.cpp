// Rvalue and forwarding references. The && of a declarator declares a
// reference, no operator (ISO/IEC 14882:2020 [dcl.ref]), so of these
// functions only each_ref decides anything: its range-for loop.
struct Widget {
    int v;
};

struct Range {
    int* begin();
    int* end();
};

void use(Widget& w);
void sink(Widget&& w);
void use_int(int& x);
Widget make();

template <typename F>
void apply(F f) {
    Widget w{1};
    f(w);
}

void take(Widget&& w) {
    use(w);
}

template <typename T>
void pass(T&& value) {
    use(value);
}

void bind_local() {
    auto&& w = make();
    use(w);
}

void each_ref(Range& r) {
    for (auto&& x : r) {
        use_int(x);
    }
}

void cast_ref(Widget w) {
    sink(static_cast<Widget&&>(w));
}

void lambda_ref() {
    apply([](auto&& x) { use(x); });
}
