"""Signatures that run past their first `)` (issue #72).

A return annotation opened on the def line and closed on a later one, a line
break after a default that holds brackets, a backslash before the return
annotation, and a long constructor signature shaped like subprocess.Popen's.
"""


def split_pair(text: str, sep: str = ",") -> tuple[
    str, str
]:
    if sep in text:
        left, right = text.split(sep, 1)
        return left, right
    return text, ""


def make(name, *, bases=(),
         skip=frozenset(), strict=False):
    kept = [base for base in bases if base not in skip]
    if strict and not kept:
        raise ValueError(name)
    return name, kept


def joined(parts, sep) \
        -> str:
    if not parts:
        return ""
    return sep.join(parts)


class Launcher:
    def __init__(self, args, bufsize=-1, executable=None,
                 stdin=None, stdout=None, stderr=None,
                 preexec_fn=None, close_fds=True,
                 shell=False, cwd=None, env=None, universal_newlines=None,
                 startupinfo=None, creationflags=0,
                 restore_signals=True, start_new_session=False,
                 pass_fds=(), *, user=None, group=None, extra_groups=None,
                 encoding=None, errors=None, text=None, umask=-1, pipesize=-1,
                 process_group=None):
        self.args = args
        if shell and not isinstance(args, str):
            self.args = " ".join(args)
        if text or encoding or errors or universal_newlines:
            self.mode = "text"
        else:
            self.mode = "bytes"
        if cwd is not None and env is not None:
            self.where = (cwd, len(env))
        else:
            self.where = (cwd, 0)
