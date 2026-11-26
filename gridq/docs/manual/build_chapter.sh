#!/usr/bin/env bash
# Compile ONE chapter standalone against the frozen preamble, to verify it builds.
# Usage (run from the manual root):  bash build_chapter.sh chapters/04-ingestion.tex
# Prints EXIT=0 on success; otherwise lists the real errors. Cross-ref "??" warnings are fine.
set -u
CH="$1"
base=$(basename "$CH" .tex)
mkdir -p build/ch
wrap="_wrap_${base}.tex"
cat > "$wrap" <<EOF
\\documentclass[11pt]{report}
\\input{preamble}
\\begin{document}
\\input{${CH%.tex}}
\\end{document}
EOF
latexmk -pdf -interaction=nonstopmode -outdir=build/ch -jobname="ck_${base}" "$wrap" > "build/ch/ck_${base}.log" 2>&1
code=$?
rm -f "$wrap"
echo "EXIT=$code"
echo "----- errors / warnings (empty = clean) -----"
grep -nE "^! |Undefined control sequence|Runaway argument|Missing [^ ]+ inserted|Extra [}\$]|Too many|LaTeX Error|Package .* Error|Emergency stop|! Dimension|! Paragraph ended" "build/ch/ck_${base}.log" | head -50
echo "----- overfull boxes (cosmetic, fix only the large ones) -----"
grep -cE "Overfull \\\\hbox" "build/ch/ck_${base}.log" | sed 's/^/overfull_hbox_count=/'
echo "PAGES=$(pdfinfo "build/ch/ck_${base}.pdf" 2>/dev/null | awk '/^Pages/{print $2}')"
