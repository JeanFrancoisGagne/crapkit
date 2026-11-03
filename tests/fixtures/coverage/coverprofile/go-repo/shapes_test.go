package shapes

import "testing"

func TestClassify(t *testing.T) {
	if Classify(-1) != "negative" || Classify(5) != "positive" {
		t.Fatal("Classify")
	}
}

func TestSumPositive(t *testing.T) {
	if SumPositive([]int{1, -2, 3}) != 4 {
		t.Fatal("SumPositive")
	}
}

func TestScaled(t *testing.T) {
	if got := Scaled([]int{2}, 3); got[0] != 6 {
		t.Fatal("Scaled")
	}
}

func TestArea(t *testing.T) {
	if Area(2, 3) != 6 {
		t.Fatal("Area")
	}
}
