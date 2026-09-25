#include <stdexcept>

int branches(int a) {
    if (a > 2) {
        return 3;
    } else if (a > 1) {
        return 2;
    } else {
        return 1;
    }
}

int pick(int k) {
    switch (k) {
    case 1:
        return 10;
    case 2:
        return 20;
    case 3:
        return 30;
    default:
        return 0;
    }
}

int logic(int a, int b, int c, int d) {
    if (a && b && c || d) {
        return 1;
    }
    return 0;
}

int choose(int a) {
    return a > 0 ? 1 : 2;
}

int loops(int n) {
    int i = 0;
    while (i < n) {
        i++;
    }
    do {
        i--;
    } while (i > 0);
    return i;
}

int outer(int n) {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (i * j > n) {
                goto done;
            }
        }
    }
done:
    return 0;
}

int fact(int n) {
    if (n <= 1) {
        return 1;
    }
    return n * fact(n - 1);
}

int guarded(void (*r)()) {
    try {
        r();
    } catch (const std::logic_error &e) {
        return 1;
    } catch (...) {
        return 2;
    }
    return 0;
}
