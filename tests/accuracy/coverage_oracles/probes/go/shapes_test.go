package probe

import (
	"os"
	"testing"
)

// TestDrive makes the calls ground_truth.tsv's stmts_run column is worked from
// when PROBE_SCENARIO is call, and none in idle.
func TestDrive(t *testing.T) {
	if os.Getenv("PROBE_SCENARIO") != "call" {
		return
	}
	IfElse(true)
	Logical(0, 1)
	Pick(1)
	Loop(0)
	Branchless(1)
	OneLine(1)
	WithClosure(4)
	Box{Value: 5}.Method(2)
}
