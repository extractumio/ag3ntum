# Ag3ntum

**Your Ops Engineer. SysAdmin + DevOps + Webmaster. Sandboxed.**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python 3.13+">
  <img src="https://img.shields.io/badge/License-AGPL%20%7C%20Commercial-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Security-6%20Layer%20Defense-orange.svg" alt="Security">
  <img src="https://img.shields.io/badge/Self--Hosted-Data%20Sovereign-blueviolet.svg" alt="Self-Hosted">
</p>

Ag3ntum is a self-hosted **Ops Engineer** for Linux servers and websites. It performs server configuration, security hardening, log analysis, website troubleshooting, and routine maintenance — methodically, traceably, and inside a 6-layer security sandbox.

Describe what you need in plain English. Ag3ntum executes with domain expertise, reports every step, and requires your approval before anything destructive runs. Full audit trail. Complete session replay. All data stays on your infrastructure.

**Model-agnostic.** Ships with Anthropic Claude support and a built-in LLM proxy that routes to any OpenAI-compatible API — OpenRouter, Azure OpenAI, Amazon Bedrock (via gateway), Google Vertex AI (via gateway), Ollama, llama.cpp, vLLM, or any local inference server. Swap models per task without code changes.

<p align="center">
  <img src="./docs/artifacts/ag3ntum_ui.gif" alt="Ag3ntum Web UI" width="800">
</p>

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/extractumio/ag3ntum/main/install.sh | bash
```

Open **https://localhost:50080** after build. See [QUICK-START-GUIDE.md](QUICK-START-GUIDE.md) for API key setup and first user creation.

**Requirements:** Docker + Docker Compose | 2GB+ RAM | Linux or macOS

---

## Capabilities

### Server Management

- **Security hardening** — SSH config audit, firewall review, fail2ban setup, SUID binary scanning, user account audit
- **Patch management** — Inventory pending updates, apply with approval, verify services restart cleanly
- **Log analysis** — Parse nginx, syslog, auth.log, application logs. Detect anomalies, correlate events, produce actionable summaries
- **Health assessment** — Service status, disk/memory/CPU usage, SSL certificate expiry, open port enumeration
- **Incident response** — Diagnose outage, identify root cause, apply fix, verify recovery, generate structured report

### Website Maintenance

- **CMS lifecycle** — WordPress/Joomla/Drupal plugin and core updates, compatibility checks, rollback on failure
- **Malware remediation** — Scan PHP/JS files for injections, quarantine infections, remove backdoors, harden file permissions
- **Database optimization** — Clean transients, optimize tables, identify slow queries, reduce bloat
- **SSL management** — Monitor certificate expiry across domains, handle renewals, verify installation
- **Performance diagnostics** — Identify bottlenecks, review cache config, analyze resource utilization

### DevOps & Infrastructure

- **Container review** — Docker Compose analysis, resource utilization, image optimization, right-sizing
- **Configuration drift** — Compare environments, flag inconsistencies, document differences
- **Dependency auditing** — Outdated packages, known CVEs, license compliance
- **Deployment support** — Analyze failures, check dependencies, review application logs
- **Infrastructure documentation** — Service inventory, port audit, cron job listing, automated system documentation

### Document Processing

Upload and process directly in the web UI:

| Format | Capability |
|--------|-----------|
| PDF | Text extraction with page-level access |
| Office | DOCX, XLSX, PPTX parsing and data extraction |
| Archives | ZIP, TAR, 7z exploration and content analysis |
| Tabular | CSV, Excel, Parquet transformation and analysis |

---

## How It Works

```
Operator ─── Web UI / REST API ─── Ag3ntum ─── Infrastructure
              (describe task)       (execute)    (local mounts, SSH, SFTP)
```

1. **Deploy** — Docker Compose on your server. 15 minutes from zero to running.
2. **Describe** — State the objective in plain English. No playbooks, no DSL.
3. **Observe** — Real-time streaming of every command, output, and decision.
4. **Approve** — Destructive operations pause for explicit human confirmation.
5. **Audit** — Complete session trail with drill-down into every tool invocation.

---

## Security Architecture

Six independent layers of defense-in-depth. Each operates autonomously — compromising one does not weaken the others.

```
Request → [WAF] → [Docker] → [Bubblewrap+UID] → [PathValidator] → [CommandFilter] → [SecureOutput]
           │         │              │                    │                │                │
       Size limits  Container    Per-user OS         Workspace        140+ blocked     Secrets
       DoS block    boundary     identity (UID)      boundary only    patterns         auto-redacted
```

| Layer | Enforcement |
|-------|------------|
| **WAF** | Request body size limits, upload filtering, Content-Length spoofing prevention, DoS mitigation |
| **Docker** | Container boundary, resource limits, read-only source mount (`:ro`), container-level seccomp profile |
| **Bubblewrap + UID** | Per-user Linux UID (50000–60000), PID/IPC/mount namespace isolation, `--clearenv`, per-session seccomp |
| **PathValidator** | All file ops restricted to session workspace, symlink escape detection, cross-user access blocked at kernel level |
| **Command Filter** | 140+ regex patterns across 16 categories: privilege escalation, destructive ops, container escape, data exfiltration, persistence mechanisms |
| **Secure Output** | API keys, tokens, and passwords auto-redacted in file previews and command output. Same-length replacement. |

### Containment in practice

| Vector | Outcome |
|--------|---------|
| `rm -rf /` | Blocked by command filter before execution |
| Read `~/.ssh/id_rsa` | Path not mounted in sandbox — invisible to agent |
| `sudo chmod 777 /etc/passwd` | Command filter blocks; seccomp denies at kernel level |
| `curl http://169.254.169.254/` | Cloud metadata IP blocked by domain filter |
| Cross-user file access | Kernel-enforced UID separation + PathValidator |
| Fork bomb `:(){ :\|:& };:` | Command filter + namespace resource limits |
| `env` / `printenv` | `--clearenv` strips all host environment variables |

AI models hallucinate. That is a known property of the technology. Ag3ntum does not solve hallucination — it solves the **blast radius** of hallucination. When the model makes a mistake, the sandbox contains the impact before it reaches your system.

---

## Multi-Tenant Isolation

Tenant separation is enforced by the Linux kernel, not application logic. Bypassing the application layer does not grant cross-tenant access.

| Layer | Mechanism |
|-------|-----------|
| **OS identity** | Unique Linux UID per user (50000–60000 range), allocated at account creation |
| **Filesystem** | Per-session workspace, per-user home directory, 660/770 permissions (no world access) |
| **Process** | PID namespace isolation — users cannot enumerate or signal each other's processes |
| **Environment** | Per-user API keys and secrets injected into sandbox only, invisible to other tenants |
| **Audit** | Complete per-user session history, tamper-resistant (agent cannot modify its own logs) |

---

## Web Interface

- **Real-time streaming** — Server-Sent Events with sequence-based deduplication, automatic reconnection with exponential backoff, polling fallback
- **Tool call transparency** — Expand any action to inspect exact command, stdin, stdout, stderr, exit code
- **File explorer** — Browse workspace files with syntax-highlighted preview, drag-and-drop upload, one-click download
- **Human-in-the-loop** — Agent pauses execution and prompts for approval before destructive operations. Answers persist — respond hours later without blocking resources
- **Session management** — Full history, resume interrupted sessions, per-session cost and token tracking
- **LLM model selection** — Switch models per session via dropdown. Route to any configured provider without restart.

---

## Platform

| Feature | Details |
|---------|---------|
| **Task queue** | Redis-backed priority queue. Default quotas: 4 global concurrent, 2 per user, 50 daily. All configurable. |
| **Auto-resume** | Tasks in progress at shutdown automatically resume on container restart |
| **Cost tracking** | Per-session input/output/cache token counts with USD cost estimation |
| **Checkpoints** | File state captured at each tool invocation — rewind to any previous state |
| **Custom skills** | Reusable operation templates (global or per-user), discoverable by the agent at runtime |
| **External mounts** | Read-only or read-write host directory access with per-user authorization |
| **Dynamic mounts** | Per-session mount requests with allowlist/blocklist authorization and subpath depth limits |
| **Custom roles** | Jinja2 prompt templates define agent behavior per deployment or per task |
| **LLM proxy** | Built-in multi-provider routing — Anthropic (native), OpenAI, OpenRouter, Azure OpenAI, or any OpenAI-compatible endpoint (Ollama, llama.cpp, vLLM). Configure in YAML. |
| **REST API** | Every UI action available programmatically. JWT auth. Full session and event API. |
| **Subagents** | Delegate sub-tasks to specialized agents with scoped tool access and isolated context |

---

## Target Audience

| Segment | Scale | Primary pain | Ag3ntum value |
|---------|-------|-------------|---------------|
| **Website operators** | 1–20 sites, WordPress/WooCommerce/Joomla | Midnight outages, malware, plugin conflicts, SSL expiry | Diagnoses and remediates site issues on command. Manages updates. Cleans infections. |
| **VPS / server owners** | 3–10 servers, developer-sysadmin hybrid | Patches pile up, logs unchecked, SSH hardening deferred | Handles the maintenance backlog methodically. Patches, audits, hardens — you review results. |
| **Hosting companies** | 40+ servers, 500+ customer sites | 3-person ops team bottlenecked on tier-1 operations | Multi-tenant automation with per-customer UID isolation and exportable audit trails. |

---

## Stack

| Component | Technology |
|-----------|------------|
| Runtime | Python 3.13+, FastAPI, Uvicorn |
| Frontend | React 18, TypeScript 5.6, Vite |
| Database | SQLite (sessions, users, events) |
| Cache & Events | Redis 7 (real-time streaming, task queue, rate limiting) |
| Sandbox | Bubblewrap, seccomp profiles (3 tiers), Linux namespaces |
| Agent Core | Claude Code Agent SDK with 11 custom MCP tools |
| LLM Proxy | Multi-provider routing (Anthropic, OpenAI-compatible, OpenRouter) |
| Deployment | Docker Compose on Ubuntu 24.04 |

---

## Quick Reference

```bash
./run.sh build                           # Build image, start all services
./run.sh restart                         # Restart (picks up code/config changes)
./run.sh rebuild                         # Full teardown and rebuild
./run.sh create-user                     # Provision user account with UID allocation
./run.sh test                            # Run full test suite (backend + security + UI)
./run.sh test --quick                    # Skip E2E and slow tests
./run.sh shell                           # Interactive shell into API container
```

**After build:** Web UI at `https://localhost:50080` | API at `https://localhost:40080`

---

## License

- **AGPL-3.0** — Open source and personal use
- **Commercial License** — Hosting companies, SaaS, enterprise deployments

**Contact:** [info@extractum.io](mailto:info@extractum.io)

---

Built on the <a href="https://platform.claude.com/docs/en/get-started">Claude Code Agent SDK</a>
