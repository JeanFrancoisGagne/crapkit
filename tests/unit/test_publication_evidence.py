"""Public evidence admission and retained-byte contracts with small ZIP fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('publication', ROOT / 'tools/evidence/publication.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def fixture(tmp_path):
    source = tmp_path / 'original.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('kept.txt', b'Exact UTF-8: Jos\xc3\xa9\r\n\x00')
        archive.writestr('withheld.txt', b'Historical fixture input')
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_public_copy_preserves_all_retained_bytes_and_the_original(tmp_path):
    source, expected = fixture(tmp_path)
    before = source.read_bytes()
    output = tmp_path / 'public.zip'
    result = p.publish(source, output, ('withheld.txt',), expected)
    assert source.read_bytes() == before
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {'kept.txt', 'PUBLICATION.json'}
        assert archive.read('kept.txt') == b'Exact UTF-8: Jos\xc3\xa9\r\n\x00'
    omitted, = result['publication']['omitted']
    assert omitted == {'path': 'withheld.txt', 'bytes': 24,
                       'sha256': hashlib.sha256(b'Historical fixture input').hexdigest()}
    assert result['publication']['original_archive']['sha256'] == expected


def test_publication_is_deterministic(tmp_path):
    source, expected = fixture(tmp_path)
    first, second = tmp_path / 'one.zip', tmp_path / 'two.zip'
    p.publish(source, first, ('withheld.txt',), expected)
    p.publish(source, second, ('withheld.txt',), expected)
    assert first.read_bytes() == second.read_bytes()


def test_saved_receipt_checks_the_public_archive(tmp_path):
    source, expected = fixture(tmp_path)
    target = tmp_path / 'public.zip'
    result = p.publish(source, target, ('withheld.txt',), expected)
    assert p.check_saved(target, expected, ('withheld.txt',)) == result


@pytest.mark.parametrize('change', ['original', 'omitted', 'receipt'])
def test_saved_check_refuses_a_different_original_omission_or_archive_hash(tmp_path, change):
    source, expected = fixture(tmp_path)
    target = tmp_path / 'public.zip'
    result = p.publish(source, target, ('withheld.txt',), expected)
    omitted = ('withheld.txt',)
    if change == 'original':
        expected = '0' * 64
    elif change == 'omitted':
        omitted = ()
    else:
        result['archive']['sha256'] = '0' * 64
        target.with_suffix('.json').write_text(json.dumps(result))
    with pytest.raises(ValueError):
        p.check_saved(target, expected, omitted)


def test_duplicate_original_members_refuse_before_publication(tmp_path):
    source, _ = fixture(tmp_path)
    with pytest.warns(UserWarning, match='Duplicate'):
        with zipfile.ZipFile(source, 'a') as archive:
            archive.writestr('kept.txt', b'Second value')
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='duplicate or damaged'):
        p.publish(source, tmp_path / 'public.zip', ('withheld.txt',), expected)


def test_changed_original_refuses_before_creating_public_output(tmp_path):
    source, _ = fixture(tmp_path)
    target = tmp_path / 'public.zip'
    with pytest.raises(ValueError, match='recorded SHA256'):
        p.publish(source, target, ('withheld.txt',), '0' * 64)
    assert not target.exists()


@pytest.mark.parametrize('omitted', [('missing.txt',), ('withheld.txt', 'withheld.txt')])
def test_wrong_omission_inventory_refuses_before_publication(tmp_path, omitted):
    source, expected = fixture(tmp_path)
    target = tmp_path / 'public.zip'
    with pytest.raises(ValueError, match='omission list'):
        p.publish(source, target, omitted, expected)
    assert not target.exists()


def test_original_cannot_be_the_public_target(tmp_path):
    source, expected = fixture(tmp_path)
    with pytest.raises(ValueError, match='must not replace'):
        p.publish(source, source, ('withheld.txt',), expected)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize('change', ['missing', 'extra', 'changed', 'manifest'])
def test_public_check_rejects_inventory_content_or_manifest_changes(tmp_path, change):
    source, expected = fixture(tmp_path)
    target = tmp_path / 'public.zip'
    result = p.publish(source, target, ('withheld.txt',), expected)
    with zipfile.ZipFile(target) as archive:
        data = {name: archive.read(name) for name in archive.namelist()}
    if change == 'missing':
        del data['kept.txt']
    elif change == 'extra':
        data['extra.txt'] = b'Unexpected'
    elif change == 'changed':
        data['kept.txt'] = b'Substituted'
    else:
        data['PUBLICATION.json'] = b'{}'
    with zipfile.ZipFile(target, 'w') as archive:
        for name, value in data.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError):
        p.verify_public(target, result['publication'])
