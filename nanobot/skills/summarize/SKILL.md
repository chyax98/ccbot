---
name: summarize
description: Summarize or extract text/transcripts from URLs, podcasts, and local files.
metadata: {"nanobot":{"emoji":"🧾","requires":{"bins":["summarize"]}}}
---

# Summarize

Use the `Bash` tool to run the `summarize` CLI.

## Quick start

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube transcript

```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

## Useful flags

- `--length short|medium|long|xl`
- `--extract-only` (URLs only)
- `--json` (machine readable)
