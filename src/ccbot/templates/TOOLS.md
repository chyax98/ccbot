# Tool Usage Notes

Tool signatures are provided automatically. This file documents non-obvious constraints.

## Bash — Shell Execution

- Use for: grep, curl, gh CLI, tmux, brew, npm, git, etc.
- Prefer targeted commands over long pipelines.

## Read / Write / Edit — File Operations

- `Read`: read a file before modifying it.
- `Write`: create or fully overwrite a file.
- `Edit`: make precise string replacements in a file.
- Always read before editing to avoid data loss.

## WebFetch / WebSearch — Web Access

- `WebFetch`: fetch a URL and extract its content.
- `WebSearch`: search the web (returns snippets + URLs).
