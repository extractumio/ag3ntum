---
name: create-python-code
description: |
  Use ONLY for complex Python tasks requiring reusable scripts, multi-file processing, or data pipelines.
  DO NOT USE for: one-off calculations, web fetch + process, simple file transforms, or tasks under 30 lines.
  For simple tasks, just write inline Python directly with mcp__ag3ntum__Bash without loading this skill.
---

# Python Code Execution Guide

## STOP - Do You Actually Need This Skill?

**Before loading this skill, ask yourself:**

| Task Type | Use This Skill? | Instead Do |
|-----------|-----------------|------------|
| One-off calculation | **NO** | Inline `python3 << 'EOF'` directly |
| Fetch URL + process | **NO** | `mcp__ag3ntum__WebFetch` + inline Python |
| Simple file transform | **NO** | Inline Python (< 30 lines) |
| Data pipeline (CSV/JSON) | Maybe | Only if > 50 lines or reusable |
| Multi-file batch processing | **YES** | Create script file |
| Reusable utility script | **YES** | Create script file |
| Complex API integration | **YES** | Create script file |

**Rule:** If the task can be done in < 30 lines of inline Python, DON'T USE THIS SKILL. Just do it directly.

---

## Quick Inline Execution (Default Approach)

For most tasks, use inline heredoc - no script files needed:

```bash
mcp__ag3ntum__Bash:
  command: |
    python3 << 'EOF'
    import json
    from pathlib import Path

    # Your code here (keep it under 30-50 lines)
    result = {"success": True, "data": {}}

    print(json.dumps(result))
    EOF
```

**That's it.** No TodoWrite. No script files. No validation ceremony.

---

## When to Create Script Files

Only create a `.py` file when:

1. Code exceeds 50 lines
2. Script will be reused multiple times
3. Complex imports/dependencies require debugging
4. Multi-stage processing with intermediate outputs

```bash
# Only for complex/reusable scripts:
mkdir -p ./scripts ./output
cat > ./scripts/processor.py << 'EOF'
#!/usr/bin/env python3
# ... complex code ...
EOF
python3 ./scripts/processor.py
```

---

## Environment Quick Reference

| Item | Value |
|------|-------|
| Python | `python3` (or `/venv/bin/python3`) |
| Writable | `./` and `./output/` only |
| Read-only | `/venv`, `/skills` |
| No pip install | All packages pre-installed |

### Available Packages (Pre-installed)

**Data:** `pandas`, `pyyaml`, `pydantic`, `jinja2`
**HTTP:** `httpx`, `requests`
**AI:** `anthropic`, `openai`, `google-genai`
**Utils:** `python-dotenv`, `pypandoc`

---

## Output Rules

1. **Always JSON:** `print(json.dumps(result))`
2. **Compact:** No verbose logging, no decorative output
3. **File offload:** Large outputs → `./output/file.json`, return path only

```python
# GOOD
print(json.dumps({"success": True, "rows": 100, "file": "./output/data.csv"}))

# BAD
print("Successfully processed 100 rows!")
print("="*50)
```

---

## Security

- Paths must stay in `./` workspace
- No `eval()`, `exec()` with user input
- No system directory access (`/etc`, `/root`, etc.)
- Environment variables: access specific keys only, don't iterate

---

## Examples

### Example 1: Simple Data Processing (Inline)

```bash
mcp__ag3ntum__Bash:
  command: |
    python3 << 'EOF'
    import json
    import pandas as pd
    from pathlib import Path

    Path("./output").mkdir(exist_ok=True)
    df = pd.read_csv("./data.csv")
    summary = df.groupby("category")["amount"].sum().to_dict()
    Path("./output/summary.json").write_text(json.dumps(summary))
    print(json.dumps({"success": True, "categories": len(summary)}))
    EOF
```

### Example 2: Web Content Processing (Inline)

```bash
# First: fetch with WebFetch tool
mcp__ag3ntum__WebFetch:
  url: "https://example.com"
  prompt: "Extract the main text content"

# Then: process inline
mcp__ag3ntum__Bash:
  command: |
    python3 << 'EOF'
    import json
    from collections import Counter
    import re

    text = """[paste WebFetch result here]"""
    words = re.findall(r'\b\w+\b', text.lower())
    freq = Counter(words).most_common(20)
    print(json.dumps({"word_count": len(words), "top_20": dict(freq)}))
    EOF
```

### Example 3: API Call (Inline)

```bash
mcp__ag3ntum__Bash:
  command: |
    python3 << 'EOF'
    import json, os, httpx

    key = os.environ.get("API_KEY", "")
    if not key:
        print(json.dumps({"error": "API_KEY not set"}))
        exit(1)

    r = httpx.get("https://api.example.com/data", headers={"Authorization": f"Bearer {key}"})
    print(json.dumps({"success": True, "items": len(r.json())}))
    EOF
```

---

## Checklist (For Complex Scripts Only)

Only use this checklist for scripts > 50 lines:

1. ☐ Create `./scripts/` and `./output/` directories
2. ☐ Write script to `./scripts/name.py`
3. ☐ Validate: `python3 -m py_compile ./scripts/name.py`
4. ☐ Execute: `python3 ./scripts/name.py`
5. ☐ Verify output in `./output/`

**For simple tasks (< 30 lines): Skip all of this. Just run inline Python.**
