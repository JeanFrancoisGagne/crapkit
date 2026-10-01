"""A Latin-1 locale row fails, not skips, where CI requires its locale.

The Latin-1 rows build their locale with localedef, and a host without the
/usr/share/i18n sources skips them. CI's Linux jobs set
CRAPKIT_REQUIRE_LOCALES=1, so a runner image that lost those sources turns
every such row red instead of leaving the whole Linux Latin-1 matrix skipped
behind a green run.
"""
import sys

import pytest

import legacy_locale
from legacy_locale import LATIN1, REQUIRE, latin1_env, missing


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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Latin-1 rows build a POSIX locale")
def test_a_localedef_without_its_charmaps_skips_the_row(tmp_path, monkeypatch):
    """localedef with no /usr/share/i18n/charmaps (the accuracy image) exits 1
    and still leaves the locale's directory behind, empty. The row skips as
    any host that cannot build the locale does; it used to fail the child's
    encoding probe, which reads ANSI_X3.4-1968 from the C locale."""
    monkeypatch.delenv(REQUIRE, raising=False)
    fake = tmp_path / "localedef"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nmkdir -p "$last"\nexit 1\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(legacy_locale.shutil, "which", lambda name: str(fake))

    with pytest.raises(pytest.skip.Exception) as skipped:
        latin1_env(tmp_path / "locales")

    assert str(skipped.value) == (f"localedef could not build {LATIN1}: "
                                  "the host lacks /usr/share/i18n sources")
