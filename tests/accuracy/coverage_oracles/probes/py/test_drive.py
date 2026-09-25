"""pytest entry for the pytest-cov producers: one test per scenario."""
import drive


def test_call():
    drive.call()


def test_idle():
    pass
