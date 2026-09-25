package equivalence

func Straight(a int) int {
	b := a + 1
	return b
}

func FourDeep(a, b, c, d bool) int {
	if a {
		if b {
			if c {
				if d {
					return 1
				}
			}
		}
	}
	return 0
}

func NestedLoops(n int) int {
	t := 0
	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			for k := 0; k < n; k++ {
				t++
			}
		}
	}
	return t
}

func FlatSeven(a int) int {
	n := 0
	if a == 1 {
		n++
	}
	if a == 2 {
		n++
	}
	if a == 3 {
		n++
	}
	if a == 4 {
		n++
	}
	if a == 5 {
		n++
	}
	if a == 6 {
		n++
	}
	if a == 7 {
		n++
	}
	return n
}
