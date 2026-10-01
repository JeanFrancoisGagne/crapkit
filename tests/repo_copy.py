"""A test repository copied while git may still hold a lock in it.

git 2.47 and later start `git maintenance run --auto` detached after a commit,
and that process holds .git/objects/maintenance.lock for a moment after the
commit returns. A fixture that commits and a test that copies its repository
raced it: shutil.copytree listed the lock, the process removed it, and the copy
failed with FileNotFoundError in the dogfood job. A lock belongs to the process
that took it, never to the repository's content, so a copy leaves every *.lock
out.
"""
import shutil


def copy_repo(src, dst, **options):
    """shutil.copytree of a repository, without the lock files git takes."""
    return shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.lock"), **options)
