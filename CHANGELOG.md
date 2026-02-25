# Changelog

All notable changes to Ag3ntum are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-25

### Added
- Production build mode — frontend pre-built during Docker image creation, eliminating npm install at startup and reducing container start time (dev mode with Vite HMR still available via `--dev` flag)
- Active tool display in web UI spinner — shows currently executing tool name (e.g., "Reading file...") instead of generic "Processing..."
- Subagent status tracking — color-coded dots and live list of active subagents in the web console during multi-agent execution
- Clipboard fallback for HTTP deployments — copy buttons now work without HTTPS via `document.execCommand` fallback
- CI/CD pipeline with self-hosted runner — full lint, structural, audit, and Docker test suite runs on every PR

### Fixed
- Web container crash-loop on fresh install — fixed Vite permission errors when source mount is read-only (EXT-2)
- User login failure after repeated install — fixed stale group/shadow entries left by previous user deletion cycles
- Session PermissionError after user creation — API container now automatically restarts to pick up new user's group membership
- Incorrect file ownership on fresh install — fixed GID lookup to use actual primary GID from passwd instead of assuming GID == UID
- `create_image` skill permission error — added `PYTHONDONTWRITEBYTECODE=1` to sandbox to prevent `__pycache__` writes on read-only `/skills` mount
- Stale dynamic mount entries in web UI — mount selector now validates localStorage state against server on load
- Status border incorrectly shown on all messages — now only applied to the last agent message
- File popup closing during text selection — fixed mousedown/click origin tracking on overlay

### Changed
- Replaced `--subset` test flag with first-class `--core` flag alongside `--backend`, `--security`, `--sandboxing`
- Explicit production vs development deployment modes with separate Docker compose overlays and entrypoint logic
- Configuration files now have two validation tiers: REQUIRED_SECRET (fail with instructions) and REQUIRED_SAFE (auto-create from template)
- Vite config migrated from TypeScript to ESM JavaScript (.mjs) for read-only source mount compatibility

### Security
- Patched CVE-2024-53981 (python-multipart), GHSA-r6ph-v2qm-q3c2 (cryptography), GHSA-cfh3-3jmp-rvhc (Pillow)
- Enforced `/src` as read-only mount in production containers
- Made `/skills` directory world-readable for unprivileged sandbox UIDs

## [0.1.0] - 2026-02-13

### Added
- Initial versioned release
- Automated release pipeline with GitHub Actions
- Version displayed in health endpoint and Docker image labels
