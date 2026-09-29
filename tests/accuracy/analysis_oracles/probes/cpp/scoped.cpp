// Scopes and declarations: which functions C++ defines (ISO/IEC 14882:2020
// [dcl.fct.def.general]), whom each one calls, and what its parameters are.
struct Tag {};
int all(Tag t);
void fail();

namespace detail {

// Direct recursion in a namespace.
long long pow10(unsigned n) {
    return n == 0 ? 1 : 10 * pow10(n - 1);
}

}  // namespace detail

struct Calc {
    // Direct recursion in a class body.
    int fact(int n) {
        if (n <= 1) {
            return 1;
        }
        return n * fact(n - 1);
    }

    // Defined as defaulted and as deleted: no body to measure.
    Calc() = default;
    Calc(const Calc&) = delete;

    // A declaration whose trailing return type holds a braced initializer.
    static auto check(int) -> decltype(all(Tag{}));
};

struct File {
    explicit File(int fd);
    File open(int fd);
};

// An out-of-class member that constructs its own class: no recursion.
File File::open(int fd) {
    if (fd == -1) {
        fail();
    }
    return File(fd);
}

// A local class with a member function of its own.
int outer(int a) {
    struct Local {
        int twice(int x) {
            if (x) {
                return 2 * x;
            }
            return 0;
        }
    };
    return Local().twice(a);
}

// A conditional expression in the body of a braceless if.
int pick(int a, int b) {
    if (a) b = b > 0 ? 1 : 2;
    return b;
}

// Parameters with no name.
int first(int*, char) {
    return 0;
}

// A reference to an array.
int head(const int (&arr)[4]) {
    return arr[0];
}
