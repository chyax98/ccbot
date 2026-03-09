---
name: pdf
description: Read, extract text from, merge, split, or create PDF files. Use when the user provides a PDF path or URL, or wants to produce a PDF output.
metadata: {"ccbot":{"emoji":"📄","requires":{"bins":["curl"]}}}
---

# PDF Skill

## Read / Extract Text

### Option 1: pdftotext (fastest, from poppler-utils)

```bash
# Install if needed: apt install poppler-utils / brew install poppler
pdftotext /path/to/file.pdf -          # stdout
pdftotext -f 3 -l 7 file.pdf -        # pages 3-7 only
pdftotext -layout file.pdf -           # preserve layout
```

### Option 2: Python (pymupdf — no install needed if in env)

```bash
uv run --with pymupdf python3 - <<'EOF'
import fitz, sys
doc = fitz.open(sys.argv[1])
for page in doc:
    print(page.get_text())
EOF /path/to/file.pdf
```

### Option 3: Read PDF from URL

```bash
TMP=$(mktemp /tmp/ccbot-XXXXXX.pdf)
curl -sL "https://example.com/report.pdf" -o "$TMP"
pdftotext "$TMP" -
rm "$TMP"
```

## Merge PDFs

```bash
uv run --with pypdf python3 - <<'EOF'
from pypdf import PdfWriter
import sys
writer = PdfWriter()
for path in sys.argv[1:]:
    writer.append(path)
with open("merged.pdf", "wb") as f:
    writer.write(f)
print("Saved: merged.pdf")
EOF file1.pdf file2.pdf file3.pdf
```

## Split PDF (extract pages)

```bash
uv run --with pypdf python3 - <<'EOF'
from pypdf import PdfReader, PdfWriter
import sys
reader = PdfReader(sys.argv[1])
start, end = int(sys.argv[2])-1, int(sys.argv[3])
writer = PdfWriter()
for i in range(start, min(end, len(reader.pages))):
    writer.add_page(reader.pages[i])
out = f"pages_{sys.argv[2]}-{sys.argv[3]}.pdf"
with open(out, "wb") as f:
    writer.write(f)
print(f"Saved: {out}")
EOF input.pdf 3 7
```

## Summarize a PDF

For long PDFs, extract text then summarize:

```bash
TEXT=$(pdftotext file.pdf -)
echo "$TEXT" | head -c 8000   # feed to context or summarize skill
```

## Output PDFs to User

Write to `output/` directory — ccbot will upload via Feishu automatically:

```bash
mkdir -p output
cp result.pdf output/result.pdf
```
