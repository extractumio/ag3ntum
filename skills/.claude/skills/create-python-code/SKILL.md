---
name: generate-python-code
description: Generate and execute Python scripts with agent. Always use it when you need to generate a python code or script.
---

### Sandbox Environment

The script runs inside a sandboxed environment with specific mounted directories:

| Mount Point | Access | Description |
|-------------|--------|-------------|
| `/workspace` | **Read-Write** | Working directory for scripts, output, and data |
| `/venv` | Read-Only | Pre-configured Python virtual environment |
| `/skills` | Read-Only | Global skill scripts and resources |
| `/user-skills` | Read-Only | User-specific skill scripts |

**Python Interpreter:**
- Located at `/venv/bin/python3`
- PATH includes `/venv/bin`, so `python3` works directly
- All packages from Available Modules are pre-installed

**Calling Other Skills:**

Skills are available at `.claude/skills/<skill_name>/` in the workspace:

```bash
# Execute a skill script
python3 .claude/skills/create_image/image_gen.py "prompt" -o ./output/image.png

# Import from a skill in Python
import sys
sys.path.insert(0, '.claude/skills/create_image')
from image_gen import generate_image
```

### Workspace Boundaries

| Constraint | Value |
|------------|-------|
| **Working Directory** | `./` (relative) or `/workspace` (absolute) |
| **Scripts Location** | `./scripts/` |
| **Output Location** | `./output/` |

> ⚠️ **CRITICAL:** Never access paths outside the workspace. Use relative paths (`./`) for all file operations.

### System Limitations

- **No package installation** at runtime (`pip install` forbidden) — all packages are pre-installed in `/venv`
- **No system command execution** outside approved patterns
- **No access** to `/etc`, `/root`, `/home`, or system directories
- **Read-only mounts:** `/venv`, `/skills`, `/user-skills` cannot be modified

---

## Environment Variables

Users may provide environment variables (API tokens, configuration) accessible via `os.environ`.

### Accessing Environment Variables

```python
import os

# Safe access with defaults
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
openai_key = os.environ.get("OPENAI_API_KEY", "")
google_key = os.environ.get("GOOGLE_API_KEY", "")

# Validate required variables
if not api_key:
    raise ValueError("ANTHROPIC_API_KEY environment variable not set")
```

---

## Output Design for LLM Consumption

Scripts must produce output optimized for LLM context windows—practical, compact, and semantically dense. The output serves as input for subsequent reasoning and task execution, not human readability.

### Principles

| Principle | Description |
|-----------|-------------|
| **Compactness** | Minimize token usage; remove redundancy, whitespace bloat, and decorative formatting |
| **Structured Data** | Use JSON, CSV, or key-value formats that LLMs parse reliably |
| **Semantic Density** | Every token should carry meaningful information; eliminate filler text |
| **Actionable Content** | Output should directly inform next steps without requiring transformation |
| **Bounded Size** | Limit output length to fit within context constraints; paginate or summarize large datasets |

### Output Format Guidelines

| Data Type | Recommended Format | Avoid |
|-----------|-------------------|-------|
| Structured results | JSON (minified or 2-space indent max) | Verbose nested XML, pretty-printed tables |
| Tabular data | CSV or JSON arrays | ASCII art tables, markdown tables for large sets |
| Status/Progress | Single-line JSON: `{"status":"done","count":42}` | Multi-line banners, decorative separators |
| Errors | `{"error":"message","code":"ERR_001"}` | Stack traces (unless debugging), verbose explanations |
| Lists | JSON arrays or comma-separated | Bulleted/numbered markdown lists |

### Anti-Patterns

- **Verbose logging:** Avoid `INFO: Starting process...`, `INFO: Step 1 complete...` unless explicitly needed for debugging
- **Decorative output:** No banners (`====`), boxes, emojis, or ASCII art
- **Redundant labels:** `{"result": {"data": {"value": 42}}}` → `{"value": 42}`
- **Human narratives:** `"The script successfully processed 100 records and found 3 errors"` → `{"processed":100,"errors":3}`
- **Unbounded output:** Dumping entire datasets without limits or summarization

### Size Management

| Scenario | Strategy |
|----------|----------|
| Large datasets (>100 rows) | Output summary stats + sample (first/last N rows) + file path for full data |
| Long text content | Truncate with `"...[truncated, full output: ./output/file.txt]"` |
| Multiple results | Aggregate into single JSON object, not multiple print statements |
| Iterative processing | Emit progress only at milestones (10%, 50%, 100%), not per-item |

### Output Structure Template

```json
{
  "success": true,
  "data": { },
  "summary": { "total": 0, "processed": 0, "errors": 0 },
  "files": ["./output/result.json"],
  "errors": []
}
```

### Key Rules

1. **Default to JSON** — universally parseable, token-efficient, unambiguous
2. **Flatten when possible** — reduce nesting depth to 2-3 levels maximum
3. **Omit null/empty fields** — don't include `"field": null` or `"items": []` unless meaningful
4. **Use short keys** — `cnt` vs `record_count` when context is clear (balance with clarity)
5. **Single output point** — one `print(json.dumps(result))` at script end, not scattered outputs
6. **File offloading** — write large outputs to `./output/`, return only the path and summary in stdout

---

## Execution Methods

> **Note:** Python is available as `python3` (PATH includes `/venv/bin`). For explicit paths, use `/venv/bin/python3`.

### Method 1: Runtime-Generated (Inline)

**Use for:** Quick operations (< 50 lines), single-purpose, one-off tasks.

```bash
python3 -c "
import json
data = {'status': 'success'}
print(json.dumps(data))
"
```

### Method 2: File-Based

**Use for:** Complex operations (> 30 lines), reusable utilities, debugging needed.

```bash
mkdir -p ./scripts ./output
cat > ./scripts/my_script.py << 'EOF'
#!/usr/bin/env python3
import json
from pathlib import Path

# All imports from Available Modules work - packages are pre-installed
import pandas as pd
import httpx

result = {"status": "success"}
print(json.dumps(result))
EOF

python3 -m py_compile ./scripts/my_script.py  # Validate syntax
python3 ./scripts/my_script.py                 # Execute
```

### Quick Start Template

For immediate script execution with proper error handling:

```bash
python3 << 'EOF'
import json
import sys
from pathlib import Path

WORKSPACE = Path("./").resolve()
OUTPUT_DIR = WORKSPACE / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

try:
    # Your code here
    result = {"success": True, "data": {}}
except Exception as e:
    result = {"success": False, "error": str(e)}

print(json.dumps(result))
EOF
```

---

## Available Modules

### Data Processing
| Module | Version | Use Case |
|--------|---------|----------|
| `pandas` | 2.3.3 | DataFrames, CSV/Excel, data analysis |
| `pydantic` | 2.12.5 | Data validation, schemas |
| `pyyaml` | 6.0.3 | YAML parsing/generation |
| `jinja2` | 3.1.6 | Template rendering |

### Web & HTTP
| Module | Version | Use Case |
|--------|---------|----------|
| `httpx` | 0.28.1 | HTTP client (sync/async) |
| `requests` | 2.32.5 | HTTP client (sync) |
| `python-multipart` | 0.0.21 | Form data parsing |

### Database
| Module | Version | Use Case |
|--------|---------|----------|
| `sqlalchemy` | 2.0.45 | ORM, database abstraction |
| `aiosqlite` | 0.22.1 | Async SQLite |

### Security & Auth
| Module | Version | Use Case |
|--------|---------|----------|
| `bcrypt` | 4.2.1 | Password hashing |

### AI/LLM SDKs
| Module | Version | Use Case |
|--------|---------|----------|
| `anthropic` | 0.76.0 | Claude API |
| `openai` | 2.15.0 | OpenAI API |
| `google-genai` | 1.59.0 | Google AI API |

### Utilities
| Module | Version | Use Case |
|--------|---------|----------|
| `python-dotenv` | 1.2.1 | Env file loading |
| `pandoc` | 2.4 | Document conversion |

### Testing & Validation
| Module | Version | Use Case |
|--------|---------|----------|
| `flake8` | 7.3.0 | Syntax/lint validation |

---

## Security Rules

### 🚫 FORBIDDEN Operations

| Category | Forbidden Actions |
|----------|-------------------|
| **System Info** | `os.uname()`, `platform.platform()`, iterating `os.environ` |
| **Path Traversal** | `../`, `/etc/`, `/root/`, `/home/`, absolute system paths |
| **Code Injection** | `eval(user_input)`, `exec(user_input)`, `os.system(user_input)` |
| **Network Attacks** | Port scanning, DDoS, unauthorized external requests |
| **Resource Exhaustion** | Infinite loops, memory bombs, fork bombs |

### ✅ REQUIRED Practices

| Practice | Implementation |
|----------|----------------|
| **Path Validation** | Resolve and verify paths stay within `./` workspace |
| **Input Sanitization** | Strip dangerous characters from user inputs |
| **Resource Limits** | Set timeouts, limit memory/iterations |
| **Error Handling** | Try/except with proper logging |
| **Env Var Access** | Only access specific, expected variables |

### Path Validation Pattern

```python
from pathlib import Path

WORKSPACE = Path("./").resolve()

def safe_path(user_path: str) -> Path:
    requested = (WORKSPACE / user_path).resolve()
    if not str(requested).startswith(str(WORKSPACE)):
        raise ValueError(f"Path traversal detected: {user_path}")
    return requested
```

---

## Syntax Validation

Shall be used for **complex scripts** or multi-file scripts.

| Scenario | Validate? | Command |
|----------|-----------|---------|
| Quick inline (< 20 lines) | No | Direct execution |
| Complex logic (> 30 lines) | **Yes** | `python3 -m py_compile script.py` |
| Production scripts | **Yes** | `python3 -m flake8 script.py --select=E9,F63,F7,F82` |

**Flake8 critical checks:** `E9` (syntax errors), `F63` (invalid print), `F7` (type comment errors), `F82` (undefined names)

---

## Output Handling

| Pattern | Use Case |
|---------|----------|
| `print(json.dumps(result))` | Programmatic consumption (preferred) |
| `Path("./output/file.json").write_text(...)` | Large results to file |
| `logging.info(...)` | Debugging/audit trail |
| `print(...)` | Simple text output |

**Always:** Create output directory first with `mkdir -p ./output`

---

## Decision Matrix

| Factor | Runtime (`python -c`) | File-Based |
|--------|----------------------|------------|
| Lines of code | < 30 | > 30 |
| Reusability | One-time | Reusable |
| Debugging | Minimal | Important |
| Complex imports | Few | Multiple |
| Validation needed | No | Yes |

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError` | Package not in pre-installed list | Use only modules from Available Modules section |
| `Permission denied` writing to `/venv` | Mount is read-only | Write only to `./` or `./output/` |
| `python3: command not found` | Unlikely, but possible PATH issue | Use explicit `/venv/bin/python3` |
| Script blocked by security | Pattern matched security filter | Check command against Security Rules |
| `No such file: ./image_gen.py` | Script not in workspace | Use full path: `python3 .claude/skills/create_image/image_gen.py` |

### Verify Environment

Quick check that environment is working:

```bash
python3 -c "import sys; print({'python': sys.executable, 'version': sys.version.split()[0]})"
```

Expected output: `{'python': '/venv/bin/python3', 'version': '3.x.x'}`

### Check Available Packages

```bash
python3 -c "import pkg_resources; print([p.key for p in pkg_resources.working_set][:10])"
```

---