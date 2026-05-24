#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_ZIP="${1:-$ROOT_DIR/../paper_arxiv_submission.zip}"

cd "$ROOT_DIR"

# Remove LaTeX intermediate/build artifacts in source tree.
find . -type f \( \
  -name "*.aux" -o \
  -name "*.bbl" -o \
  -name "*.blg" -o \
  -name "*.log" -o \
  -name "*.out" -o \
  -name "*.toc" -o \
  -name "*.fls" -o \
  -name "*.fdb_latexmk" -o \
  -name "*.synctex.gz" \
\) -delete

# Remove generated top-level PDF only (keep figures/*.pdf source assets).
rm -f arxiv_main.pdf
rm -f "$OUT_ZIP"

# White-list packaging for arXiv upload.
zip -r "$OUT_ZIP" \
  arxiv_main.tex \
  acl.sty \
  acl_natbib.bst \
  custom.bib \
  capter \
  table \
  figures

echo "Created: $OUT_ZIP"
