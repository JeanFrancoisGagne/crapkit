#include "shapes.h"

int main() {
    return clamp(5, 0, 10) == 5 && clamp(-1, 0, 10) == 0 ? 0 : 1;
}
