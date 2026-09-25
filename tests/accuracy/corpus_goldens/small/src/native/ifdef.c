/* #ifdef twins: one name defined once per platform branch. */
#include <stdio.h>
#include "util.h"

#ifdef _WIN32
int open_log(const char *path, int append)
{
    if (path == NULL) {
        return -1;
    }
    if (append) {
        return 2;
    }
    return 1;
}
#else
int open_log(const char *path, int append)
{
    if (path == NULL || path[0] == '\0') {
        return -1;
    }
    return append ? 2 : 1;
}
#endif

int classify(int code)
{
    switch (code) {
    case 0:
        return 0;
    case 1:
    case 2:
        return 1;
    case 3:
        return 2;
    default:
        return clamp_int(code, 0, 9);
    }
}
