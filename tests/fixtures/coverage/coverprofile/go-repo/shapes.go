// Package shapes is the coverprofile fixture: branches, a loop, a func
// literal, a call into an internal package and a function no test calls.
package shapes

import "example.com/shapes/internal/calc"

// Classify names the sign of n.
func Classify(n int) string {
	if n < 0 {
		return "negative"
	}
	if n == 0 {
		return "zero"
	}
	return "positive"
}

// SumPositive adds the positive numbers in xs.
func SumPositive(xs []int) int {
	total := 0
	for _, x := range xs {
		if x > 0 {
			total += x
		}
	}
	return total
}

// Scaled returns each x times k through a func literal.
func Scaled(xs []int, k int) []int {
	scale := func(x int) int {
		if x == 0 {
			return 0
		}
		return x * k
	}
	out := make([]int, 0, len(xs))
	for _, x := range xs {
		out = append(out, scale(x))
	}
	return out
}

// Area multiplies through the internal package.
func Area(w, h int) int {
	return calc.Mul(w, h)
}

// Unused is never called by a test.
func Unused(a, b int) int {
	if a > b {
		return a - b
	}
	return b - a
}
