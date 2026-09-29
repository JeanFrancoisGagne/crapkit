"""Every hand probe: crapkit's number for one function against a value worked
from an outside text before crapkit ran.

The probe files under probes/<lang>/ are measured once through the CLI
(conftest `probe_inventory`). Each probes.tsv row names a function, a metric
and the value its `source` column derives: the McCabe text in NIST SP 500-235,
the Sonar cognitive complexity paper, lizard's documented column definitions
or the language reference. A row whose `ruling` is set pins a recorded
difference instead (rulings.tsv): a definition crapkit documents, or a defect
that stays a strict xfail until it is fixed.
"""
import pytest

from accuracy.analysis_oracles import analysis_tables

pytestmark = pytest.mark.process
PROBES = analysis_tables.probes()


@pytest.mark.parametrize("probe", [pytest.param(probe, id=probe.key,
                                                marks=analysis_tables.marks(probe.ruling))
                                   for probe in PROBES])
def test_crapkit_matches_the_hand_value(probe, probe_inventory):
    actual = analysis_tables.value(probe_inventory, probe)

    analysis_tables.check(actual, probe.expected, probe.ruling)


def test_every_probe_file_is_named_by_a_probe():
    named = {probe.path for probe in PROBES}

    assert sorted(set(analysis_tables.probe_files()) - named) == []
