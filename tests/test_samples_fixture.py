"""Guards the fix for the bug JJ reported on 2026-09-16.

The defect was not that four scripts forgot to generate the sample PDFs. It was that
forgetting had **no consequence** until somebody cloned the repository, because the files sit
untracked on every machine that has ever run the generator. Three scripts carried their own
private copy of the helper and four did not, and nothing connected the two facts.

So the real fix is not the helper. It is this file, which fails when an eighth script reads
the samples directory without going through the helper.
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, EVAL_DIR)

from samples_fixture import FIRST_SAMPLE, SAMPLES_DIR, ensure_samples, sample_path  # noqa: E402

# The helper itself defines the directory, so it is not required to import itself.
EXEMPT = {"samples_fixture.py"}


def eval_modules():
    return sorted(f for f in os.listdir(EVAL_DIR)
                  if f.endswith(".py") and f not in EXEMPT)


def reads_samples(source):
    """True when the module refers to the samples directory at all."""
    return "SAMPLES_DIR" in source or "samples_fixture" in source


def imports_the_helper(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "samples_fixture":
            return True
        if isinstance(node, ast.Import):
            if any(alias.name == "samples_fixture" for alias in node.names):
                return True
    return False


@pytest.mark.parametrize("module", eval_modules())
def test_any_script_reading_samples_goes_through_the_helper(module):
    """The guard. An eighth script that forgets will fail here rather than on someone's clone."""
    source = open(os.path.join(EVAL_DIR, module), encoding="utf-8").read()
    if not reads_samples(source):
        pytest.skip(f"{module} does not read the samples directory")
    assert imports_the_helper(source), (
        f"{module} refers to the samples directory but does not import samples_fixture. "
        "The PDFs are gitignored and absent on a fresh clone, so this will raise "
        "FileNotFoundError for anyone but you. Import ensure_samples or sample_path.")


def test_only_one_definition_of_the_helper_exists():
    """Three copies is how the four omissions went unnoticed. There must stay exactly one."""
    definitions = [m for m in os.listdir(EVAL_DIR) if m.endswith(".py")
                   if "def ensure_samples" in open(os.path.join(EVAL_DIR, m), encoding="utf-8").read()]
    assert definitions == ["samples_fixture.py"], (
        f"ensure_samples is defined in {definitions}. Copies drift: the three that existed "
        "before 2026-09-16 had three slightly different bodies.")


def test_ensure_samples_is_idempotent(tmp_path):
    """Called twice it must not regenerate, or every run would rewrite files needlessly."""
    target = str(tmp_path / "samples")
    ensure_samples(target)
    first = {name: os.path.getmtime(os.path.join(target, name)) for name in os.listdir(target)}
    assert first, "nothing was generated"
    ensure_samples(target)
    second = {name: os.path.getmtime(os.path.join(target, name)) for name in os.listdir(target)}
    assert first == second


def test_it_generates_into_an_empty_directory(tmp_path):
    """The clean-clone case: the directory does not exist at all."""
    target = str(tmp_path / "does_not_exist_yet")
    assert not os.path.isdir(target)
    ensure_samples(target)
    assert any(f.endswith(".pdf") for f in os.listdir(target))


def test_the_first_sample_is_readable_after_the_helper_runs():
    """The exact file whose absence produced JJ's six errors."""
    path = sample_path()
    assert os.path.basename(path) == FIRST_SAMPLE
    assert os.path.exists(path)
    assert os.path.getsize(path) > 0


def test_the_samples_are_not_tracked_by_git():
    """If someone 'fixes' this by committing the PDFs, this test says why not.

    ReportLab stamps a random /ID into every file, so an identical invoice produces different
    bytes each time and git would report an unchanged document as modified on every run.
    """
    import subprocess
    tracked = subprocess.run(
        ["git", "ls-files", "evaluation/samples/"],
        cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    assert tracked == "", (
        f"generated PDFs are tracked: {tracked}. They should stay gitignored and be produced "
        "by samples_fixture.ensure_samples(); see sample-fixture-spec.md.")
