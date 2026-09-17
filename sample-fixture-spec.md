# Spec: remove the test suite's dependency on an untracked PDF

**Status: awaiting Neo's approval. Nothing implemented.**
Written 2026-09-16, after JJ reported six test errors on a clean checkout.

## The defect, reproduced

JJ is right, and the report is accurate in every detail. Reproduced on a fresh clone of
`neo/gate-verification`:

```
279 passed, 6 errors in 1.59s
ERROR tests/test_error_taxonomy.py  (all six)
FileNotFoundError: evaluation/samples/sample_invoice_1_INV-2026-001.pdf
```

`evaluation/samples/` **does not exist at all** in a clean clone. `.gitignore:13` ignores
`*.pdf` globally, so the three generated sample invoices have never been tracked.

**The claim "285 tests pass" was therefore environment-dependent and should not have been
made without checking it on a clean checkout.** That is the same failure this project
documents elsewhere: a number repeated from a local run is not a measurement of the
repository.

## The defect is larger than the tests

`load_documents()` in `sentinel_comparison.py` reads the PDF with no guard. So does the
script itself, not only the test that imports it. **On a clean clone
`evaluation/sentinel_comparison.py` fails the same way**, and fixing only the test would
leave the script broken while making the suite green, which is worse than the current state.

Surveying every script in `evaluation/`:

| Script | Reads `samples/` | Has a guard |
|---|---|---|
| `run_eval.py` | yes | own copy of `ensure_samples()` |
| `model_compatibility.py` | yes | own copy, subtly different |
| `prompt_schema_2x2.py` | yes | own copy, subtly different |
| `sentinel_comparison.py` | yes | **none** |
| `schema_comparison.py` | yes | **none** |
| `error_taxonomy.py` | yes | **none** |
| `gate_verification_preview.py` | yes | **none** |

**Three copies of the same helper, four scripts that forgot it.** The duplication is the
root cause: there is no single place to add the guard, so each new script has to remember,
and four did not. Fixing the one script JJ happened to hit would leave the same trap for the
next one.

## Options considered

**A. Commit the PDFs through a `.gitignore` exception.** Rejected. These files are generated
output, and the project has already decided against tracking generated binaries after the
`.docx` problem, where a ZIP container stamps each entry with the write time and reports an
unchanged document as modified. ReportLab writes a random `/ID` into every PDF, so the same
invoice produces different bytes on every generation. Tracking them would produce spurious
diffs for exactly the reason recorded in `database-spec.md`.

**B. Inline the extracted text into `load_documents()`.** Rejected as the primary fix. The
other four documents in that function are already inline strings, so it would be consistent.
But the first document exists specifically to be **real text that came through `pypdf` from a
real PDF**, and the measurement in §5.5 was run against that path. Replacing it with a string
would quietly change what was measured while leaving the numbers in the report unchanged.

**C. One shared helper, used by all seven scripts.** Proposed.

## Proposed change

Add `evaluation/samples_fixture.py` with a single `ensure_samples()`, and have all seven
scripts call it. It generates the three PDFs into `evaluation/samples/` if and only if no PDF
is there, which is what the three existing copies already do. `reportlab==5.0.1` and
`pypdf==6.16.2` are both already pinned in `requirements.txt`, so this adds no dependency.

The three existing copies are deleted and replaced by the import. The four scripts that lack
a guard gain one.

For the tests specifically, the fixture is invoked once per session through a
`tests/conftest.py` fixture rather than at import time, so that collecting tests never writes
to disk and only the tests that need the PDFs cause them to be generated.

## Why not a temporary directory, which is what JJ suggested

JJ's suggestion is reasonable and was considered. A `tmp_path` fixture would make the tests
hermetic, which is a genuine improvement over writing into the working tree.

It is not proposed as the primary fix because **the scripts have the same bug**, and a
`tmp_path` fixture fixes only the tests. Anyone cloning this repository to reproduce the §5.5
measurements would still hit `FileNotFoundError` on the first command. The shared helper fixes
both, and the tests inherit the fix for free.

If the group prefers hermetic tests as well, the two are compatible: the helper takes a target
directory, so `conftest.py` can point it at `tmp_path` while the scripts point it at
`evaluation/samples/`. This is worth doing and is included below.

## Deliverable

- `evaluation/samples_fixture.py`, one `ensure_samples(target_dir=SAMPLES_DIR)`
- Seven call sites updated, three duplicate definitions deleted
- `tests/conftest.py` with a session fixture that generates into a temporary directory
- A test that **fails if any script in `evaluation/` reads `samples/` without the guard**,
  so the eighth script cannot reintroduce this

The last item matters more than the fix. The bug was not that one script forgot; it was that
forgetting had no consequence until someone cloned the repository.

## How to know it worked

`git clone` into a clean directory, create the venv, install requirements, and run
`pytest tests/ -q`. It must report 285 passed with no errors, and
`./.venv/bin/python evaluation/sentinel_comparison.py --help` must not raise.

This is the check that should have been run before the count was quoted, and it becomes part
of the definition of done for anything touching `evaluation/`.
