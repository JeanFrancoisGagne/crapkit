// The probe's declarations, shared by shapes.cpp and drive.cpp.
int if_else(bool flag);
int logical(int a, int b);
int ternary(int x);
int loop(int n);
int branchless(int x);
int one_line(int x);
int untouched(int x);

struct Box {
    int value;
    int method(int x);
};
