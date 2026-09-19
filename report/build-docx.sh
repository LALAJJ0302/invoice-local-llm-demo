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
# The .docx is gitignored. The markdown is the source of truth, and rebuilding OVERWRITES the
# .docx, so hand edits made in Word are lost. Edit the markdown, not the Word file.

set -e
cd "$(dirname "$0")/.."

OUT="report/Neo Pitayasiri - Final Report Draft v4.docx"

pandoc \
  report/draft-v1.md \
  report/section-1-introduction.md \
  report/section-2-literature.md \
  report/section-3-problem-analysis.md \
  report/section-4-design.md \
  report/section-5-evaluation.md \
  report/section-6-discussion.md \
  report/section-7-recommendations.md \
  report/section-8-conclusion.md \
  report/section-9-references.md \
  --from=gfm \
  --toc \
  -o "$OUT"

echo "$OUT"
WORDS=$(cat report/draft-v1.md report/section-[1-9]-*.md | wc -w | tr -d ' ')
echo "$WORDS words across 10 source files"
