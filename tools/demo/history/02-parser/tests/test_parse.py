from calc.parse import coerce


def test_coerce_reads_an_integer():
    assert coerce(" 42 ") == 42
