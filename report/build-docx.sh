#!/bin/bash
# Builds the Word deliverable from the markdown sources, in the correct order.
#
#     ./report/build-docx.sh
#
# Order matters and is the reason this script exists. `draft-v1.md` now carries only the YAML
# front matter and the draft-status block, so it must come first; every numbered section lives
# in its own file. A glob such as `report/section-*.md` sorts lexically and happens to be
# correct today, but it silently breaks the moment a section number reaches double digits, so
# the files are listed explicitly.
#
# The list lives in SOURCES and is used twice: once to build and once to count. It used to be
# written out twice, once in the pandoc invocation and once as `section-[1-9]-*.md` in the word
# count, and on 2026-09-28 both silently missed `section-03-requirements.md` and
# `section-10-limitations.md`. The build reported success and produced a report short of 3,851
# words. One list, read twice, is the fix: a file that is not built is not counted either, so
# the two can no longer disagree.
#
# The .docx is gitignored. The markdown is the source of truth, and rebuilding OVERWRITES the
# .docx, so hand edits made in Word are lost. Edit the markdown, not the Word file.
#
# ORDERING NOTE. This is the twelve-section structure of report-outline-v2.md, adopted on
# 2026-09-29. Three of its sections are not written and are listed below as comments at the
# position they will occupy, so that the gap is visible in the file that builds the report
# rather than only in the outline. A commented line is not a source: it is not built and not
# counted, which is the behaviour wanted until someone writes the section.

set -e
cd "$(dirname "$0")/.."

OUT="report/Neo Pitayasiri - Final Report Draft v4.docx"

SOURCES=(
  report/draft-v1.md
  report/section-01-introduction.md
  report/section-02-literature.md
  report/section-03-requirements.md
  report/section-04-architecture.md
# report/section-05-security-privacy.md     5. Security and Privacy      waiting on Luke
  report/section-06-implementation.md
# report/section-07-rag-method.md           7. RAG Method                waiting on JJ
  report/section-08-evaluation.md
# report/section-09-deployment.md           9. Deployment                waiting on Luke
  report/section-10-limitations.md
  report/section-11-discussion.md
  report/section-12-conclusion.md
  report/section-99-references.md
)

# A missing source is a silently shorter report, so fail instead of building one.
for source in "${SOURCES[@]}"; do
  [ -f "$source" ] || { echo "[!] Missing source: $source" >&2; exit 1; }
done

pandoc "${SOURCES[@]}" \
  --from=gfm \
  --toc \
  -o "$OUT"

echo "$OUT"
WORDS=$(cat "${SOURCES[@]}" | wc -w | tr -d ' ')
echo "$WORDS words across ${#SOURCES[@]} source files"
