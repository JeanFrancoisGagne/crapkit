"""A Latin-1 locale row fails, not skips, where CI requires its locale.

The Latin-1 rows build their locale with localedef, and a host without the
/usr/share/i18n sources skips them. CI's Linux jobs set
CRAPKIT_REQUIRE_LOCALES=1, so a runner image that lost those sources turns
every such row red instead of leaving the whole Linux Latin-1 matrix skipped
behind a green run.
"""
import pytest

from legacy_locale import REQUIRE, missing


def test_a_host_without_the_locale_skips_the_row(monkeypatch):
    monkeypatch.delenv(REQUIRE, raising=False)

    with pytest.raises(pytest.skip.Exception) as skipped:
        missing("localedef could not build en_US.ISO-8859-1")

    assert str(skipped.value) == "localedef could not build en_US.ISO-8859-1"


def test_a_job_that_requires_the_locale_fails_the_row_naming_the_fix(monkeypatch):
    monkeypatch.setenv(REQUIRE, "1")

    with pytest.raises(pytest.fail.Exception) as failed:
        missing("localedef could not build en_US.ISO-8859-1")

    assert str(failed.value) == ("localedef could not build en_US.ISO-8859-1; CRAPKIT_REQUIRE_LOCALES=1 "
                                 "says this job must build it (apt-get install locales)")
