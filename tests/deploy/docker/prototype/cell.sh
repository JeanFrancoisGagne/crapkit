set -eu
ms() { echo $(( ($(date +%s%N)-T0)/1000000 )); }
export PIP_NO_INDEX=1 PIP_FIND_LINKS=/wheelhouse
T0=$(date +%s%N)
python3.12 -m venv $HOME/v && . $HOME/v/bin/activate
pip install -q "crapkit==$1"; echo "install $1: $(ms) ms"; crapkit --version
mkdir r && cd r && git init -q -b main
mkdir -p calc tests && printf 'def grade(s, a, late, bonus):\n    if s > 90:\n        return "A"\n    if s > 80 and not late:\n        return "B"\n    if bonus:\n        return "C"\n    return "F"\n' > calc/grade.py && touch calc/__init__.py
printf 'from calc.grade import grade\ndef test_a():\n    assert grade(95,1,False,False)=="A"\n' > tests/test_grade.py
printf "[pytest]
testpaths = tests
" > pytest.ini
git add -A && git -c user.email=t@t -c user.name=t commit -qm init
pip install -q pytest pytest-cov
T0=$(date +%s%N); crapkit init; echo "init: $(ms) ms"
T0=$(date +%s%N); crapkit doctor >/dev/null; echo "doctor exit=$? $(ms) ms"
set +e
T0=$(date +%s%N); crapkit coverage; echo "coverage (no container_ok) exit=$? $(ms) ms"
set -e
python - <<'P'
import re,pathlib
p=pathlib.Path('crapkit.toml'); t=p.read_text()
t=t.replace('[[lane]]\n', '[[lane]]\ncontainer_ok = true\n', 1); p.write_text(t)
P
T0=$(date +%s%N); crapkit coverage; echo "coverage exit=$? $(ms) ms"
T0=$(date +%s%N); crapkit worklist | head -3; echo "worklist: $(ms) ms"
T0=$(date +%s%N); crapkit ratchet seed; echo "seed: $(ms) ms"
git add -A >/dev/null; git -c user.email=t@t -c user.name=t commit -qm adopt
if [ "${2:-}" ]; then
  T0=$(date +%s%N); pip install -q --upgrade "crapkit==$2"; echo "upgrade to $2: $(ms) ms"; crapkit --version
  set +e
  T0=$(date +%s%N); crapkit doctor >/dev/null; echo "doctor exit=$? $(ms) ms"
  T0=$(date +%s%N); crapkit verify 2>&1 | tail -2; echo "verify before reseed exit=$? $(ms) ms"
  T0=$(date +%s%N); crapkit coverage | tail -1; crapkit ratchet prune | tail -1; crapkit ratchet seed | tail -1; echo "guide steps: $(ms) ms"
  git add -A; git -c user.email=t@t -c user.name=t commit -qm reseed >/dev/null
  T0=$(date +%s%N); crapkit verify 2>&1 | tail -1; echo "verify exit=$? $(ms) ms"
fi
