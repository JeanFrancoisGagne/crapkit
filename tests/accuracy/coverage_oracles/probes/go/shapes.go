// Package probe holds one function per coverage shape the Go ground truth is worked for.
package probe

func IfElse(flag bool) int {
	if flag {
		return 1
	}
	return 2
}

func Logical(a, b int) int {
	if a > 0 && b > 0 {
		return 1
	}
	return 0
}

func Pick(n int) string {
	switch n {
	case 1:
		return "one"
	default:
		return "many"
	}
}

func Loop(n int) int {
	total := 0
	for i := 0; i < n; i++ {
		total += i
	}
	return total
}

func Branchless(n int) int {
	doubled := n * 2
	return doubled + 1
}

func OneLine(n int) int { return n + 1 }

func Untouched(n int) int {
	if n > 0 {
		return n
	}
	return -n
}

func WithClosure(n int) int {
	check := func(v int) int {
		if v > 0 {
			return 1
		}
		return 0
	}
	return check(n)
}

type Box struct{ Value int }

func (b Box) Method(n int) bool {
	if n < b.Value {
		return true
	}
	return false
}
