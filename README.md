# Ag3ntum

**The secure AI agent framework for production environments.**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python 3.13+">
  <img src="https://img.shields.io/badge/License-AGPL%20%7C%20Commercial-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Security-5%20Layer%20Defense-orange.svg" alt="Security">
</p>

**Ag3ntum** is a general-purpose AI agent framework with **security-first architecture**. 
Dockerized, sandboxed, secured with additional application-level filters. Every tool call passes through multiple independent security layers before execution—so even if the AI tries something dangerous, it simply can't.

Built on the Claude Agent SDK, Ag3ntum wraps AI capabilities in defense-in-depth protection that works on local machines and production servers alike.

---

## Features

### 🔒 5-Layer Security Architecture

### ⚡ Features and Capabilities

All Claude Code capabilities, plus:

- **Multi-Tenancy** — User-separated sessions with isolated workspaces
- **Secured MCP Tools** — Custom tools with built-in security validation
- **Dual Interface** — CLI for development, Web UI for production
- **Backend API** — RESTful API with JWT auth for automation and integration
- **Session Management** — Persistent sessions with checkpoints and history
- **Skills System** — Modular, reusable agent capabilities
- **Real-time Streaming** — SSE-based live execution output
 

### 🛡️ Security Features

- **Process Termination Blocked** — `kill`, `pkill`, `killall` commands filtered
- **Destructive Operations Blocked** — `rm -rf`, `chmod 777`, `mkfs` prevented
- **Privilege Escalation Blocked** — `sudo`, `su`, user management commands denied
- **Container Escape Blocked** — `docker`, `kubectl`, `nsenter` filtered
- **Path Traversal Blocked** — `../` attacks and absolute paths rejected
- **Sensitive Files Protected** — `.env`, `.key`, `.git` files inaccessible
- **Environment Cleared** — No secrets leaked via environment variables
- **Process Enumeration Hidden** — `/proc` filtered to hide other processes

### 🎯 Production Ready

- **Fail-Closed Design** — If security checks fail, operations are denied
- **Comprehensive Logging** — Full audit trail of all agent actions
- **Rate Limiting Ready** — Built for multi-tenant deployments
- **Docker Compose** — One-command deployment

---

## How It Works

```
User Request
     │
     ▼
┌─────────────────────────────────┐
│  Layer 0: Inbound WAF           │  ← Filtering of input from user request
├─────────────────────────────────┤
│  Layer 1: Command Security      │  ← Regex-based argument filtering
├─────────────────────────────────┤
│  Layer 2: Path Validator        │  ← Workspace confinement
├─────────────────────────────────┤
│  Layer 3: Bubblewrap Sandbox    │  ← Process isolation
├─────────────────────────────────┤
│  Layer 4: Docker Container      │  ← Host isolation (outermost boundary)
└─────────────────────────────────┘
     │
     ▼
  Safe Execution
```

Every layer operates independently. Even if one layer is bypassed, the others continue to protect the system.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/current_architecture.md) | System design and component overview |
| [Security Layers](docs/layers_of_security_for_filesystem.md) | Detailed security architecture |
| [WAF Filter](docs/inbound_waf_filter.md) | Inbound request filtering |

---

## License

Ag3ntum is available under a **dual license**:

### Open Source — AGPL-3.0

For open source projects and personal use, Ag3ntum is licensed under the [GNU Affero General Public License v3.0](LICENSE).

### Commercial License

For proprietary applications, SaaS products, or enterprise deployments where AGPL compliance is not possible, a commercial license is available.

**Contact:** [info@extractum.io](mailto:info@extractum.io)

---

## About

Ag3ntum is developed by **EXTRACTUM** — building secure AI products for business.

---

<p align="center">
  <strong>Don't let your AI agent become a security incident.</strong><br>
  <em>Run agents safely with Ag3ntum.</em>
</p>