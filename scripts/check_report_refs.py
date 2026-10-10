"""Check that every cross-reference in the report points at a section that exists.

    ./.venv/bin/python scripts/check_report_refs.py

The report is eleven markdown files that reference each other by section number. A renumbering
pass moves those numbers, and nothing in the test suite can tell whether a reference followed.
Worse, a wrong number often still resolves: §7.2 became §10.4, and a prefix-only rewrite would
have sent six references to §10.2, which exists and reads plausibly. This is the check that
catches that class of error.

Two reports:

  DANGLING   a §n.n reference whose target heading does not exist in any section file
  DUPLICATE  the same section number used as a heading in two files
  PENDING    a reference into a section that is planned but not written yet

PENDING is not a failure. It exists for the case where a written section points forward at one
that is planned and not drafted yet, which was true of sections 5, 7 and 9 until 29 September
2026. The dict below is empty because every planned section is now written; add an entry rather
than letting a forward reference report as DANGLING.

References naming another document are skipped. `database-spec.md` §5 is a reference into the
data-layer spec, not into section 5 of the report, and rewriting it would be a defect.

Exit status is 1 if anything DANGLING or DUPLICATE was found, so this can gate a build.
"""
import os
import re
import sys

REPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report")

# Sections are numbered; appendices are lettered. "Appendix A." and "A.3" both count.
HEADING = re.compile(r"^#{1,4}\s+(?:Appendix\s+)?([A-Z]|\d+)((?:\.\d+)*)\.?\s")
# Planned in report/report-outline-v2.md, not written. Each is blocked on a teammate.
PENDING = {}
REFERENCE = re.compile(r"§([A-Z]|\d+)((?:\.\d+)*)")
# A reference is treated as pointing outside this report when a filename appears close enough
# in front of it to be doing the addressing.
FOREIGN = re.compile(r"(?:`[^`]+\.md`|\b\w+[-_]spec\.md|README)[^.§]{0,40}$")


def section_files():
    return sorted(f for f in os.listdir(REPORT)
                  if f.startswith(("section-", "appendix-")) and f.endswith(".md"))


def scan():
    headings = {}          # number -> [file, ...]
    references = []        # (file, line number, number, line text)
    for name in section_files():
        with open(os.path.join(REPORT, name)) as fh:
            for n, line in enumerate(fh, 1):
                head = HEADING.match(line)
                if head:
                    number = head.group(1) + head.group(2)
                    headings.setdefault(number, []).append(name)
                for ref in REFERENCE.finditer(line):
                    if FOREIGN.search(line[:ref.start()]):
                        continue
                    references.append((name, n, ref.group(1) + ref.group(2), line.rstrip()))
    return headings, references


def resolves(number, headings):
    """A reference resolves if the number is a heading, or names a section that has headings.

    §8 with no subsection is a reference to the whole of section 8, which is satisfied by any
    heading beginning 8. §8.4 is satisfied by 8.4 itself or by 8.4.1 under it.
    """
    if number in headings:
        return True
    return any(h.startswith(number + ".") for h in headings)


def main():
    headings, references = scan()
    unresolved = [r for r in references if not resolves(r[2], headings)]
    pending = [r for r in unresolved if r[2].split(".")[0] in PENDING]
    dangling = [r for r in unresolved if r not in pending]
    duplicate = {n: f for n, f in headings.items() if len(set(f)) > 1}

    print(f"{len(section_files())} section files, {len(headings)} numbered headings, "
          f"{len(references)} internal references")

    if duplicate:
        print(f"\nDUPLICATE: {len(duplicate)} section numbers used in more than one file")
        for number, files in sorted(duplicate.items()):
            print(f"  §{number}: {', '.join(sorted(set(files)))}")

    if dangling:
        print(f"\nDANGLING: {len(dangling)} references to sections that do not exist")
        for name, line, number, text in dangling:
            print(f"  {name}:{line}  §{number}")
            print(f"      {text.strip()[:110]}")
    else:
        print("\nDANGLING: none")

    if pending:
        by_section = {}
        for name, line, number, _ in pending:
            by_section.setdefault(number.split(".")[0], []).append(f"{name}:{line}")
        print(f"\nPENDING: {len(pending)} references into sections not written yet")
        for top, places in sorted(by_section.items()):
            print(f"  §{top} ({PENDING[top]}): {', '.join(places)}")

    return 1 if dangling or duplicate else 0


if __name__ == "__main__":
    sys.exit(main())
