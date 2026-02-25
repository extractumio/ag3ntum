---
name: docgen
description: Describe the essential principles on how to generate comprehensive documentation on the code keeping it compact
---

# Technical Documentation Standards

## Document Overview

This guide provides comprehensive instructions for documenting software architecture and codebase internals. The resulting documentation must be precise, scannable, and immediately actionable for its intended audience.

---

## Target Audience Definition

**Primary Readers:** Senior software developers, system architects, and technical leads who:
- Possess 5+ years of professional development experience
- Understand distributed systems, design patterns, and security principles
- Need to integrate with, extend, or maintain the documented system
- Value brevity over verbosity; precision over explanation of fundamentals

**Do NOT explain:**
- Basic programming concepts (REST, HTTP methods, JSON, etc.)
- Common design patterns unless implementation deviates from standard
- Language syntax or standard library usage

**DO explain:**
- Why specific architectural decisions were made
- Trade-offs accepted and alternatives rejected
- Non-obvious behaviors and edge cases

---

## General Documentation Principles

### The Three Laws of Technical Documentation

1. **Accuracy over completeness** — Outdated documentation is worse than no documentation
2. **Scanability over prose** — Developers search, they don't read
3. **Examples over descriptions** — Show, then tell

### Content Density Rules

```
GOOD: "JWT tokens expire after 15min. Refresh tokens: 7 days, single-use, rotated on each refresh."

BAD:  "The system uses JSON Web Tokens for authentication. These tokens have
       an expiration time of fifteen minutes, which was chosen to balance
       security with user experience. Additionally, refresh tokens are
       provided which last for seven days..."
```

### The "5-Second Rule"

A senior developer should be able to extract the core information from any section within 5 seconds of looking at it. This requires:
- Bold or uppercase for critical terms
- Consistent heading hierarchy
- Information-front sentences (conclusion first, context second)

---

## Document Structure Template

Every architecture document should follow this skeleton:

```
1. SYSTEM OVERVIEW (1 paragraph max)
2. ARCHITECTURE DIAGRAM (ASCII)
3. COMPONENT BREAKDOWN
   └── For each component: Purpose → Interface → Dependencies → Failure modes
4. DATA FLOW
5. SECURITY MODEL
6. ERROR HANDLING STRATEGY
7. CONFIGURATION REFERENCE
8. KNOWN LIMITATIONS
```

---

## ASCII Diagram Standards

### Why ASCII Over Images

- Version-controllable (git diff works)
- Copy-pasteable into code comments
- No external tool dependencies
- Forces simplicity

### Diagram Types and When to Use

| Diagram Type | Use Case |
|--------------|----------|
| Box-and-arrow | Component relationships, data flow |
| Sequence | Request/response flows, multi-step processes |
| State machine | Object lifecycle, connection states |
| Tree | Hierarchies, decision trees, dependency graphs |

### Box-and-Arrow Template

```
┌─────────────┐     request      ┌─────────────┐
│   Client    │ ───────────────> │   Gateway   │
└─────────────┘                  └──────┬──────┘
                                        │
                         ┌──────────────┼──────────────┐
                         │              │              │
                         v              v              v
                  ┌──────────┐   ┌──────────┐   ┌──────────┐
                  │ Service A│   │ Service B│   │ Service C│
                  └──────────┘   └──────────┘   └──────────┘
```

### Sequence Diagram Template

```
Client          Gateway         Auth            Service
  │                │              │                │
  │── POST /login ─>              │                │
  │                │── validate ──>                │
  │                │<── token ────│                │
  │<── 200 + JWT ──│              │                │
  │                │              │                │
  │── GET /data ───>              │                │
  │  [+ Bearer]    │── verify ────>                │
  │                │<── claims ───│                │
  │                │──────────── forward ─────────>│
  │                │<─────────── response ─────────│
  │<── 200 + data ─│              │                │
```

### State Machine Template

```
                    ┌─────────────────────────────────┐
                    v                                 │
┌────────┐     ┌────────┐     ┌───────────┐     ┌─────────┐
│  INIT  │ ──> │ ACTIVE │ ──> │ DRAINING  │ ──> │ CLOSED  │
└────────┘     └────────┘     └───────────┘     └─────────┘
                    │                                 ^
                    │           ┌─────────┐           │
                    └────────── │  ERROR  │ ──────────┘
                                └─────────┘
```

---

## Section-Specific Instructions

### 1. Security Architecture

**Required Elements:**

```
SECURITY ARCHITECTURE
=====================

THREAT MODEL
------------
- Asset: [What is being protected]
- Threat actors: [Who might attack]
- Attack vectors: [How they might attack]

AUTHENTICATION
--------------
Mechanism: [e.g., JWT, OAuth2, mTLS]
Token format: [structure, claims]
Expiration policy: [access: X, refresh: Y]
Storage: [where tokens are stored client/server side]

AUTHORIZATION
-------------
Model: [RBAC/ABAC/ACL]
Enforcement point: [where checks happen]
Permission structure:
  └── [hierarchy or matrix]

ENCRYPTION
----------
In transit: [TLS version, cipher suites]
At rest: [algorithm, key management]
Key rotation: [frequency, mechanism]

SECRETS MANAGEMENT
------------------
Storage: [Vault/KMS/env vars]
Access pattern: [how services retrieve secrets]
Rotation: [automatic/manual, frequency]
```

**Checklist for Security Section:**
- [ ] Specify exact algorithms and key sizes (AES-256-GCM, RSA-2048, etc.)
- [ ] Document token/session invalidation mechanisms
- [ ] List all trust boundaries with diagram
- [ ] Include rate limiting and brute-force protections
- [ ] Specify audit logging coverage
- [ ] Document secrets that must NEVER be logged

**Example Entry:**

```
AUTHENTICATION: JWT + Refresh Token Rotation
--------------------------------------------
Problem: Stateless auth with revocation capability

Mechanism:
  Access token:  JWT, ES256 signed, 15min TTL
  Refresh token: Opaque, 256-bit random, 7-day TTL, single-use

Flow:
  1. User authenticates → receives {access_token, refresh_token}
  2. Access token used for API calls (Authorization: Bearer)
  3. On 401 → client calls /auth/refresh with refresh_token
  4. Server invalidates old refresh_token, issues new pair

Revocation:
  - Refresh tokens stored in Redis with user_id index
  - Logout: delete all refresh tokens for user_id
  - Access tokens: short TTL makes revocation unnecessary

Dependencies:
  - Redis (refresh token storage)
  - KMS (JWT signing key)
```

---

### 2. Communication Protocols

**Required Elements:**

```
PROTOCOL: [Name]
================

TRANSPORT
---------
Layer: [TCP/UDP/QUIC]
Encryption: [TLS version, cert requirements]
Port: [default port]

MESSAGE FORMAT
--------------
Encoding: [JSON/Protobuf/MessagePack]
Schema location: [path to schema files]
Versioning: [how versions are negotiated]

CONNECTION LIFECYCLE
--------------------
[State diagram]

HANDSHAKE
---------
[Sequence diagram with exact message types]

MESSAGE TYPES
-------------
| Type ID | Name        | Direction    | Purpose              |
|---------|-------------|--------------|----------------------|
| 0x01    | HELLO       | C → S        | Initiate connection  |
| 0x02    | HELLO_ACK   | S → C        | Confirm, send config |
| ...     | ...         | ...          | ...                  |

FLOW CONTROL
------------
[Mechanism description: windowing, backpressure, etc.]

ERROR HANDLING
--------------
| Error Code | Meaning          | Recovery Action       |
|------------|------------------|-----------------------|
| E001       | Invalid message  | Log, close connection |
| E002       | Rate limited     | Exponential backoff   |
```

**Checklist for Protocol Section:**
- [ ] Document byte-level message structure if binary
- [ ] Include timeout values for all operations
- [ ] Specify retry semantics (at-most-once, at-least-once, exactly-once)
- [ ] Document keepalive/heartbeat mechanism
- [ ] List all error codes with recovery procedures
- [ ] Include maximum message size limits

**Example Entry:**

```
WEBSOCKET PROTOCOL: Real-time Event Stream
==========================================

Purpose: Push server events to clients with minimal latency

Transport: WSS (WebSocket Secure) over TLS 1.3
Endpoint: wss://api.example.com/v1/stream
Auth: JWT passed as ?token= query param on connect

Message Format: JSON with envelope
{
  "type": "EVENT_TYPE",
  "id": "uuid-v4",
  "ts": 1699999999999,
  "payload": { ... }
}

Connection Flow:

  Client                         Server
    │                              │
    │── WSS connect + ?token ─────>│
    │                              │── validate JWT
    │<──────── 101 Switching ──────│
    │                              │
    │<──────── CONNECTED ──────────│   (confirms auth, sends config)
    │           {"heartbeat_ms":   │
    │            30000}            │
    │                              │
    │── SUBSCRIBE ────────────────>│   (subscribe to channels)
    │   {"channels":["orders"]}    │
    │                              │
    │<──────── SUBSCRIBED ─────────│
    │                              │
    │<──────── EVENT ──────────────│   (push events)
    │<──────── EVENT ──────────────│
    │                              │
    │── PING ─────────────────────>│   (every 30s)
    │<──────── PONG ───────────────│
    │                              │

Failure Handling:
- No PONG within 5s → client reconnects
- Server sends PING if no client message for 45s
- No response → server closes connection
- Reconnect: exponential backoff 1s, 2s, 4s, 8s, max 30s
```

---

### 3. Backend API Documentation

**Required Elements:**

```
ENDPOINT: [METHOD] [PATH]
=========================

PURPOSE
-------
[One sentence: what problem this solves]

AUTHENTICATION
--------------
Required: [Yes/No]
Scopes: [required OAuth scopes or roles]

REQUEST
-------
Headers:
  Content-Type: application/json
  Authorization: Bearer <token>
  X-Request-ID: <uuid>  [optional, for tracing]

Path Parameters:
  {id} - [type] - [description]

Query Parameters:
  | Name    | Type   | Required | Default | Description |
  |---------|--------|----------|---------|-------------|

Body:
  [JSON schema or example with annotations]

RESPONSE
--------
Success (200):
  [JSON example]

  Fields:
    .field_name - [type] - [description]

ERRORS
------
| Status | Code           | Cause                    | Resolution          |
|--------|----------------|--------------------------|---------------------|
| 400    | INVALID_INPUT  | Schema validation failed | Check request body  |
| 401    | UNAUTHORIZED   | Missing/invalid token    | Refresh auth token  |
| 404    | NOT_FOUND      | Resource doesn't exist   | Verify ID           |
| 429    | RATE_LIMITED   | Too many requests        | Retry after header  |

RATE LIMITS
-----------
[X] requests per [time window]
Scope: [per-user/per-IP/per-API-key]

IDEMPOTENCY
-----------
[Is this endpoint idempotent? How to make it so?]

SIDE EFFECTS
------------
[What else happens: events emitted, caches invalidated, etc.]
```

**Checklist for API Section:**
- [ ] Every path/query parameter documented with type and constraints
- [ ] All possible response codes listed with example bodies
- [ ] Pagination documented (cursor-based preferred, include limits)
- [ ] Specify which fields are filterable/sortable
- [ ] Note if response is cached and cache TTL
- [ ] Document any eventual consistency implications

**Example Entry:**

```
ENDPOINT: POST /v1/orders
=========================

Purpose: Create a new order from cart contents

Auth: Required. Scopes: orders:write

Request:
  POST /v1/orders
  Content-Type: application/json
  Authorization: Bearer <token>
  Idempotency-Key: <client-generated-uuid>

  {
    "cart_id": "cart_abc123",          // required, valid cart ID
    "shipping_address_id": "addr_xyz", // required, user's saved address
    "payment_method_id": "pm_456",     // required, user's saved payment
    "notes": "Leave at door"           // optional, max 500 chars
  }

Response (201 Created):
  {
    "id": "order_789",
    "status": "PENDING_PAYMENT",
    "total_cents": 4999,
    "currency": "USD",
    "created_at": "2024-01-15T10:30:00Z",
    "estimated_delivery": "2024-01-20"
  }

Errors:
  | Status | Code              | Cause                        |
  |--------|-------------------|------------------------------|
  | 400    | CART_EMPTY        | Referenced cart has no items |
  | 400    | INVALID_ADDRESS   | Address not deliverable      |
  | 402    | PAYMENT_FAILED    | Card declined                |
  | 409    | DUPLICATE_ORDER   | Idempotency-Key already used |
  | 422    | INVENTORY_UNAVAIL | Item out of stock            |

Processing Flow:

  Request ──> Validate ──> Reserve ──> Charge ──> Create ──> Response
               Cart       Inventory   Payment    Order
                │             │          │
                v             v          v
              [400]        [422]      [402]

Side Effects:
  - Event emitted: order.created (for fulfillment service)
  - Cart marked as "converted"
  - Inventory reservation held for 10 min until payment confirms

Idempotency:
  - Include Idempotency-Key header (UUID)
  - Duplicate key within 24h returns original response
  - After 24h, key can be reused
```

---

### 4. Data Models and Schemas

**Required Elements:**

```
ENTITY: [Name]
==============

PURPOSE
-------
[What this entity represents in the domain]

SCHEMA
------
| Field          | Type         | Constraints      | Description          |
|----------------|--------------|------------------|----------------------|
| id             | UUID         | PK               | Unique identifier    |
| created_at     | TIMESTAMP    | NOT NULL         | Creation time (UTC)  |
| status         | ENUM         | NOT NULL         | [list values]        |

RELATIONSHIPS
-------------
  ┌──────────┐       ┌──────────┐       ┌──────────┐
  │   User   │ 1───* │  Order   │ *───* │ Product  │
  └──────────┘       └──────────┘       └──────────┘
                           │
                           │ 1
                           │
                           *
                     ┌──────────┐
                     │ LineItem │
                     └──────────┘

INDEXES
-------
| Name                    | Columns              | Purpose                |
|-------------------------|----------------------|------------------------|
| idx_orders_user_id      | user_id              | User's order history   |
| idx_orders_status_date  | status, created_at   | Order processing queue |

STATE MACHINE
-------------
[If entity has status/state field, include state diagram]

INVARIANTS
----------
- [Business rules that must always be true]
- Example: order.total = SUM(line_items.price * quantity)

SOFT DELETE
-----------
[Yes/No. If yes, which field? How to query?]
```

**Checklist for Data Models:**
- [ ] Specify exact types (VARCHAR(255) not just "string")
- [ ] Document nullable fields explicitly
- [ ] List all enum values with meanings
- [ ] Explain denormalized fields and sync mechanisms
- [ ] Note any fields that are encrypted at rest
- [ ] Document cascade behavior for deletions

---

### 5. Error Handling Architecture

**Required Elements:**

```
ERROR HANDLING STRATEGY
=======================

ERROR CLASSIFICATION
--------------------
| Category    | Retryable | User-Facing | Alert Level |
|-------------|-----------|-------------|-------------|
| Validation  | No        | Yes         | None        |
| AuthN/AuthZ | No        | Yes         | Warn        |
| Downstream  | Yes       | Generic     | Error       |
| Internal    | Maybe     | Generic     | Critical    |

ERROR RESPONSE FORMAT
---------------------
{
  "error": {
    "code": "UNIQUE_ERROR_CODE",    // stable, for client handling
    "message": "Human readable",     // can change, for logs/debug
    "details": { ... },              // optional, structured context
    "trace_id": "abc-123"            // for support correlation
  }
}

ERROR PROPAGATION
-----------------
[How errors flow through layers]

  Controller ─── catches ───> formats as HTTP response
       ^
       │ throws
       │
  Service ─── catches ───> wraps with context, rethrows
       ^
       │ throws
       │
  Repository ─── catches ───> translates DB errors

RETRY POLICY
------------
Retryable errors: [list]
Strategy: Exponential backoff
  - Initial: 100ms
  - Multiplier: 2
  - Max attempts: 3
  - Max delay: 5s
  - Jitter: ±20%

Circuit Breaker:
  - Failure threshold: 5 failures in 10s
  - Open duration: 30s
  - Half-open: allow 1 request

LOGGING REQUIREMENTS
--------------------
| Error Type      | Log Level | Include Stack | Include Request |
|-----------------|-----------|---------------|-----------------|
| Validation      | DEBUG     | No            | Yes             |
| Auth failure    | WARN      | No            | Partial*        |
| Downstream fail | ERROR     | Yes           | Yes             |
| Unhandled       | CRITICAL  | Yes           | Yes             |

* Partial = exclude sensitive headers/body
```

---

### 6. Service Dependencies

**Required Elements:**

```
DEPENDENCY: [Service/Library Name]
==================================

PURPOSE
-------
[Why this dependency exists]

TYPE
----
[ ] Runtime (required for operation)
[ ] Build-time only
[ ] Optional (graceful degradation)

INTEGRATION PATTERN
-------------------
Communication: [HTTP/gRPC/SDK/direct]
Auth: [API key/OAuth/mTLS]
Location: [URL/service discovery]

FAILURE MODE
------------
When unavailable:
  - [What functionality is lost]
  - [How system degrades]
  - [Recovery behavior]

Timeout: [connection timeout] / [read timeout]
Retry: [policy]
Circuit breaker: [settings]

HEALTH CHECK
------------
Endpoint: [health check URL]
Frequency: [how often checked]
Failure action: [what happens on failure]

VERSION COMPATIBILITY
---------------------
Current: v2.3.x
Minimum: v2.0.0
Known issues: [any version-specific bugs]
```

**Dependency Map Example:**

```
                          EXTERNAL
                    ┌─────────────────┐
                    │  Stripe API     │
                    │  (payments)     │
                    └────────┬────────┘
                             │
  ┌──────────────────────────┼──────────────────────────┐
  │                          │           OUR SYSTEM     │
  │   ┌─────────────┐   ┌────┴─────┐   ┌─────────────┐  │
  │   │   Gateway   │──>│ Order    │──>│ Inventory   │  │
  │   │             │   │ Service  │   │ Service     │  │
  │   └──────┬──────┘   └────┬─────┘   └──────┬──────┘  │
  │          │               │                │         │
  │          v               v                v         │
  │   ┌─────────────┐   ┌─────────────┐   ┌─────────┐   │
  │   │    Redis    │   │  Postgres   │   │  Redis  │   │
  │   │   (cache)   │   │   (orders)  │   │ (locks) │   │
  │   └─────────────┘   └─────────────┘   └─────────┘   │
  └─────────────────────────────────────────────────────┘

  Legend:
    ──>  Sync call (HTTP/gRPC)
    - ->  Async (message queue)
    [C]   Critical (no degradation)
    [D]   Degradable
```

---

### 7. Configuration Reference

**Required Elements:**

```
CONFIGURATION REFERENCE
=======================

ENVIRONMENT VARIABLES
---------------------
| Variable           | Required | Default  | Description              |
|--------------------|----------|----------|--------------------------|
| DATABASE_URL       | Yes      | -        | Postgres connection URI  |
| REDIS_URL          | Yes      | -        | Redis connection URI     |
| LOG_LEVEL          | No       | info     | debug/info/warn/error    |
| MAX_CONNECTIONS    | No       | 100      | DB connection pool size  |

Format notes:
  DATABASE_URL: postgres://user:pass@host:5432/db?sslmode=require
  REDIS_URL: redis://:password@host:6379/0

FEATURE FLAGS
-------------
| Flag                  | Default | Description                    |
|-----------------------|---------|--------------------------------|
| FF_NEW_CHECKOUT       | false   | Enable v2 checkout flow        |
| FF_RATE_LIMIT_STRICT  | true    | Enforce strict rate limits     |

RUNTIME TUNABLES
----------------
These can be changed without restart:

  /config/reload  [POST]  - Reload config from source
  /config         [GET]   - View current effective config

SECRETS
-------
| Name            | Source    | Rotation    | Description           |
|-----------------|-----------|-------------|-----------------------|
| JWT_PRIVATE_KEY | Vault     | 90 days     | Token signing key     |
| STRIPE_API_KEY  | Vault     | Manual      | Payment processor     |
| DB_PASSWORD     | K8s Secret| 30 days     | Database credential   |
```

---

### 8. Deployment Architecture

**Required Elements:**

```
DEPLOYMENT ARCHITECTURE
=======================

INFRASTRUCTURE DIAGRAM
----------------------
[ASCII diagram showing regions, zones, services]

SCALING
-------
| Component      | Min | Max | Trigger           | Cooldown |
|----------------|-----|-----|-------------------|----------|
| API servers    | 3   | 20  | CPU > 70%         | 5min     |
| Workers        | 2   | 10  | Queue depth > 100 | 3min     |

RESOURCE REQUIREMENTS
---------------------
| Component      | CPU   | Memory | Storage | Network |
|----------------|-------|--------|---------|---------|
| API server     | 2 CPU | 4GB    | 10GB    | 1Gbps   |
| Worker         | 1 CPU | 2GB    | 5GB     | 100Mbps |
| Database       | 8 CPU | 32GB   | 500GB   | 10Gbps  |

HIGH AVAILABILITY
-----------------
[Describe redundancy at each layer]

DISASTER RECOVERY
-----------------
RTO: [Recovery Time Objective]
RPO: [Recovery Point Objective]
Backup frequency: [schedule]
Backup location: [where stored]
Recovery procedure: [reference to runbook]
```

---

## Happy Path / Failure Path Documentation

For critical flows, always document both paths:

```
FLOW: User Checkout
===================

HAPPY PATH
----------
  User                 API              Payment           Inventory
   │                    │                  │                  │
   │── POST /checkout ─>│                  │                  │
   │                    │── reserve ───────────────────────── >│
   │                    │<─────────────────────────── reserved─│
   │                    │── charge ────────>│                  │
   │                    │<──── success ─────│                  │
   │                    │── confirm ───────────────────────── >│
   │<─── 201 Created ───│                  │                  │

   Total time: ~2s

FAILURE PATHS
-------------

F1: Inventory unavailable
   │── POST /checkout ─>│
   │                    │── reserve ───────────────────────── >│
   │                    │<────────────────────── OUT_OF_STOCK ─│
   │<─── 422 + items ───│

   Recovery: Return unavailable item IDs, client removes from cart

F2: Payment declined
   │── POST /checkout ─>│
   │                    │── reserve ───────────────────────── >│
   │                    │<─────────────────────────── reserved─│
   │                    │── charge ────────>│
   │                    │<──── declined ────│
   │                    │── release ───────────────────────── >│
   │<─── 402 + reason ──│

   Recovery: Inventory auto-released, client prompts for new payment

F3: Timeout after payment (CRITICAL)
   │── POST /checkout ─>│
   │                    │── charge ────────>│
   │                    │     ... 30s ...   │
   │<─── 504 Timeout ───│
   │                    │<──── success ─────│  (arrives late)

   Recovery:
     - Payment recorded in pending_reconciliation table
     - Async job checks payment status after 5min
     - If charged: create order, notify user
     - If not charged: mark as abandoned
```

---

## Documentation Anti-Patterns

**AVOID:**

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| "For more info, see the code" | Not documentation | Extract essential info |
| Describing obvious getter/setters | Noise | Only document non-obvious |
| Copy-pasting similar endpoints | Maintenance burden | Use templates, note differences |
| Screenshots of diagrams | Not version-controllable | ASCII or mermaid |
| "TODO: document this" | Technical debt | Document now or remove |
| Describing implementation, not interface | Couples to code | Focus on contracts |

---

## Quality Checklist

Before considering a section complete:

```
[ ] Can a senior dev use this without reading the code?
[ ] Are all error cases documented?
[ ] Is the happy path clear in under 30 seconds?
[ ] Are timeouts/limits/thresholds specified with numbers?
[ ] Is the diagram accurate to current implementation?
[ ] Are dependencies' failure modes documented?
[ ] Is sensitive data handling explicitly addressed?
[ ] Can this survive a code refactor without full rewrite?
```

---

## Maintenance Rules

1. **Update on PR merge** — Docs live in same repo, same PR as code
2. **Quarterly review** — Verify diagrams match reality
3. **Deprecation notice** — Mark outdated sections, don't delete immediately
4. **Version stamps** — Include "Last verified: YYYY-MM-DD" on critical sections

---

This guide serves as the foundation for producing technical documentation that respects the reader's time and expertise while providing the precision needed for effective system integration and maintenance.

---

Process user input.
