package calc

import "testing"

func TestDiv(t *testing.T) {
	if q, ok := Div(4, 2); !ok || q != 2 {
		t.Fatal("Div")
	}
}
