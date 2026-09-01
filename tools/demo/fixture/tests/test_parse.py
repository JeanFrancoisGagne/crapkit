from calc.parse import coerce, parse


def test_coerce_reads_an_integer():
    assert coerce(" 42 ") == 42


def test_parse_skips_comments():
    rows = parse("# header\nada,91\n", ["name", "score"])
    assert rows == [{"name": "ada", "score": 91}]
