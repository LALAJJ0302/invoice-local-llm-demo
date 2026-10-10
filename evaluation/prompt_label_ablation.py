"""Measures one word in the extraction prompt, with every other variable held fixed.

    ./.venv/bin/python evaluation/prompt_label_ablation.py [--runs 3] [--model llama3.2]

The prompt introduces its few-shot example with `Document Content:` and then introduces the
actual invoice with `Document Content:` as well. The same label, twice, for two blocks that
mean completely different things to the reader.

This script changes the second label to `Current Document Content:` and changes nothing else,
then runs the standard evaluation both ways. Measured on 2026-09-28 across three runs each:

    Document Content:           10/15   66.7%
    Current Document Content:   15/15  100.0%

Written because the change arrived as an incidental edit inside a pull request about
retrieval, where its effect was larger than the feature the pull request was measuring, and
because a result that size should not rest on someone having noticed a diff.

**How the ablation is applied.** The prompt is a literal inside `DocumentExtractor`, so there
is no parameter to flip. This patches the module source in memory, which is why it reports the
exact substitution it made: an ablation that silently matched nothing would otherwise print two
identical columns and read as a null result.

Reads the sample documents and writes nothing.
"""
import argparse
import importlib
import io
import os
import re
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "evaluation")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, EVAL_DIR)

# The two arms. The example's own label is deliberately left alone: the point is that the two
# blocks were indistinguishable, and renaming either one of them is enough to separate them.
ORIGINAL = "Document Content:\n        \\\"\\\"\\\"{raw_text}\\\"\\\"\\\""
AMENDED = "Current Document Content:\n        \\\"\\\"\\\"{raw_text}\\\"\\\"\\\""


def load_main_with(label_source: str) -> types.ModuleType:
    """Imports `main` with the document label replaced. Raises if the text was not found."""
    source = open(os.path.join(REPO_ROOT, "main.py"), encoding="utf-8").read()
    if source.count(ORIGINAL) != 1:
        raise SystemExit(
            f"[!] Expected exactly one occurrence of the document label, found "
            f"{source.count(ORIGINAL)}. The prompt has changed; update this script rather "
            f"than trusting its output."
        )
    patched = source.replace(ORIGINAL, label_source)
    module = types.ModuleType("main_ablation")
    module.__dict__["__file__"] = os.path.join(REPO_ROOT, "main.py")
    exec(compile(patched, "main.py", "exec"), module.__dict__)
    return module


def score_once(module: types.ModuleType, model: str) -> tuple:
    """Runs the standard evaluation against the patched module. Returns (correct, total)."""
    import run_eval

    importlib.reload(run_eval)
    run_eval.DocumentExtractor = module.DocumentExtractor
    run_eval.ConfidenceValidator = module.ConfidenceValidator

    captured, original_stdout = io.StringIO(), sys.stdout
    sys.stdout = captured
    try:
        run_eval.main.__globals__["DocumentExtractor"] = module.DocumentExtractor
        run_eval.main.__globals__["ConfidenceValidator"] = module.ConfidenceValidator
        sys.argv = ["run_eval.py", "--model", model]
        try:
            run_eval.main()
        except SystemExit:
            pass
    finally:
        sys.stdout = original_stdout

    text = captured.getvalue()
    match = re.search(r"OVERALL\s+(\d+)/(\d+)", text)
    if not match:
        raise SystemExit("[!] Could not read an OVERALL line from run_eval output.")
    return int(match.group(1)), int(match.group(2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="llama3.2")
    args = parser.parse_args(argv)

    arms = [("Document Content:", ORIGINAL), ("Current Document Content:", AMENDED)]
    results = {}

    for label, source in arms:
        module = load_main_with(source)
        scores = []
        for run in range(1, args.runs + 1):
            correct, total = score_once(module, args.model)
            scores.append((correct, total))
            print(f"  {label:<28s} run {run}: {correct}/{total}")
        results[label] = scores

    print()
    print(f"{'prompt label':<28s} {'runs':<18s} mean")
    for label, scores in results.items():
        runs = " ".join(f"{c}/{t}" for c, t in scores)
        mean = sum(c for c, _ in scores) / len(scores)
        total = scores[0][1]
        print(f"{label:<28s} {runs:<18s} {mean:.1f}/{total} ({mean / total * 100:.1f}%)")

    before = sum(c for c, _ in results["Document Content:"]) / args.runs
    after = sum(c for c, _ in results["Current Document Content:"]) / args.runs
    print()
    print(f"One word moves extraction by {after - before:.1f} field-values out of "
          f"{results['Document Content:'][0][1]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
