#!/bin/bash
# Renders a markdown file in report/ to PDF, for reading away from a machine.
#
#     ./report/build-pdf.sh report/presentation.md
#
# pandoc handles the markdown, headless Chrome does the PDF. Chrome rather than a LaTeX
# engine because these documents are in Thai, and Chrome uses the system font stack, where
# Thonburi is already present on macOS. A LaTeX path needs xelatex plus a configured Thai
# font and falls over silently when either is missing.

set -e
SRC="${1:-report/presentation.md}"
[ -f "$SRC" ] || { echo "not found: $SRC"; exit 1; }

BASE="${SRC%.md}"
HTML="$BASE.tmp.html"
PDF="$BASE.pdf"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

cat > /tmp/pdf-style.css <<'CSSEOF'
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }

body {
  font-family: "Sarabun", "Noto Sans Thai", "Thonburi", -apple-system, sans-serif;
  font-size: 10.5pt;
  line-height: 1.75;
  color: #1a1a1a;
  max-width: none;
}

h1 {
  font-size: 17pt; margin: 0 0 0.6em; padding-bottom: 0.3em;
  border-bottom: 2.5px solid #1a1a1a; page-break-after: avoid;
}
h1:not(:first-of-type) { page-break-before: always; padding-top: 0; }
h2 {
  font-size: 13.5pt; margin: 1.6em 0 0.5em; padding-bottom: 0.2em;
  border-bottom: 1px solid #bbb; page-break-after: avoid;
}
h3 { font-size: 11.5pt; margin: 1.2em 0 0.4em; page-break-after: avoid; }
p, ul, ol { orphans: 3; widows: 3; }

code {
  font-family: "SF Mono", Menlo, monospace; font-size: 9pt;
  background: #f2f2f2; padding: 1px 4px; border-radius: 3px;
}
pre {
  background: #f7f7f7; border-left: 3px solid #999; border-radius: 3px;
  padding: 9px 12px; overflow-x: auto; page-break-inside: avoid;
  font-size: 8.8pt; line-height: 1.5;
}
pre code { background: none; padding: 0; font-size: inherit; }

table {
  border-collapse: collapse; width: 100%; margin: 1em 0;
  font-size: 9.3pt; page-break-inside: avoid;
}
th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #ededed; font-weight: 600; }

blockquote {
  border-left: 3px solid #666; margin: 1em 0; padding: 0.3em 0 0.3em 1em;
  color: #333; background: #fafafa;
}
hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
strong { font-weight: 700; }
CSSEOF

pandoc "$SRC" \
  --standalone \
  --from=gfm \
  --to=html5 \
  --css=/tmp/pdf-style.css \
  --embed-resources \
  --variable pagetitle="$(head -1 "$SRC" | sed 's/^#* *//')" \
  -o "$HTML"

"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PDF" "$HTML" 2>/dev/null

rm -f "$HTML" /tmp/pdf-style.css
echo "$PDF  ($(du -h "$PDF" | cut -f1))"
