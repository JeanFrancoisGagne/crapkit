// Package calc is the fixture's inner package: a -coverpkg run lists its
// blocks once per test binary.
package calc

// Mul multiplies, short-cutting a zero factor.
func Mul(a, b int) int {
	if a == 0 || b == 0 {
		return 0
	}
	return a * b
}

// Div divides, refusing a zero divisor.
func Div(a, b int) (int, bool) {
	if b == 0 {
		return 0, false
	}
	return a / b, true
}
