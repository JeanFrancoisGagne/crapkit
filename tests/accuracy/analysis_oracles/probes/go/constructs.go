package constructs

func Branches(a int) int {
	if a > 2 {
		return 3
	} else if a > 1 {
		return 2
	} else {
		return 1
	}
}

func Pick(k int) int {
	switch k {
	case 1:
		return 10
	case 2:
		return 20
	case 3:
		return 30
	default:
		return 0
	}
}

func Logic(a, b, c, d bool) int {
	if a && b && c || d {
		return 1
	}
	return 0
}

func Loops(n int) int {
	i := 0
	for i < n {
		i++
	}
	for i > 0 {
		i--
	}
	return i
}

func Outer(n int) int {
outer:
	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			if i*j > n {
				break outer
			}
		}
	}
	return 0
}

func Fact(n int) int {
	if n <= 1 {
		return 1
	}
	return n * Fact(n-1)
}
