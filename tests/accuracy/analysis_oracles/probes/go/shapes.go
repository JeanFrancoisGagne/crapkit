package shapes

var hooks []func()

func AfterFuncType(n int) int {
	return n + 1
}

func Show(v interface{}, w int) int {
	return w
}

func Each(f func(int, string) error) int {
	return 0
}

func Wrap(n int) int {
	run(func() {
		n++
	})
	return n
}

func Default(def int) int {
	return def
}

func NestedInElse(a, b bool) int {
	if a {
		return 1
	} else {
		if b {
			return 2
		}
	}
	return 0
}

func InitAfterIf(a []int) int {
	if len(a) == 0 {
		return 0
	}
	if n := len(a); n > 1 {
		for range a {
			n--
		}
	}
	return 1
}

func CaseRun(args []string, s string) int {
	for len(args) > 0 {
		switch {
		case strings.HasPrefix(s, "-") && !strings.Contains(s, "=") && len(s) == 2 && !short(s[1:], flags):
			if len(args) <= 1 {
				return 1
			}
		}
	}
	return 0
}

func DoCall(n int) int {
	do(n)
	return n
}

func LongRun(a, b, c bool) int {
	if a &&
		b && c {
		return 1
	}
	return 0
}

func Spin(n int) int {
	i := 0
	for {
		if i > n {
			break
		}
		i++
	}
	return i
}

func Pick(c, d chan int) int {
	select {
	case x := <-c:
		return x
	case y := <-d:
		return y
	default:
		return 0
	}
}
