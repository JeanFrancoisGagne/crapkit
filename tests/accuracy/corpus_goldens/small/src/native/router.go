// Go: a method, a type switch and a closure.
package native

type Route struct {
	Method string
	Path   string
}

func (r Route) Matches(method, path string) bool {
	if r.Method != "*" && r.Method != method {
		return false
	}
	return r.Path == path || r.Path == "*"
}

func Describe(v interface{}) string {
	switch x := v.(type) {
	case int:
		if x < 0 {
			return "negative"
		}
		return "int"
	case string:
		return "string"
	default:
		return "other"
	}
}
