// Drive shapes.cpp for one scenario: `call` makes exactly the calls ground_truth.tsv's
// arms_taken column is worked from; `idle` calls nothing.
#include <cstring>

#include "shapes.h"

static void call() {
    if_else(true);
    logical(0, 1);
    ternary(1);
    loop(0);
    branchless(1);
    one_line(1);
    Box box{5};
    box.method(2);
}

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "call") == 0) {
        call();
    }
    return 0;
}
