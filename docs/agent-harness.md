# Agent Harness Direction

## Why Memocore Needs a Harness

Memocore already has a narrow extraction harness. `ExtractionService` builds prompts, calls
configured providers, validates structured output, retries invalid responses, and falls back to
another provider. `CaptureService` then persists derived objects transactionally and records
events.

Future integrations and specialist workers need a broader harness. Grow it alongside real
secretary workflows rather than as a standalone infrastructure phase. The harness is the
controlled runtime around model reasoning and tool use. It must keep execution bounded,
inspectable, and recoverable. It is not a license for free-form autonomy.

## Boundary

The harness belongs in services and domain contracts, with adapters for external tools. Telegram,
calendar, email, files, and future workers should request capabilities through the same policy
boundary.

```mermaid
flowchart LR
    A["Secretary workflow"] --> B["Harness run"]
    B --> C["Build bounded context"]
    C --> D["Choose registered tool"]
    D --> E["Policy check"]
    E -->|read-only allowed| F["Execute adapter"]
    E -->|approval required| G["Create approval request"]
    G -->|approved| F
    F --> H["Record result and audit event"]
    H --> I["Stop, retry, or continue within budget"]
```

## Required Contracts

Add the full set of these concepts before adding write-capable integrations. Read-only
integrations may begin with the smaller audited boundary described in H1:

| Concept | Purpose |
|---|---|
| `HarnessRun` | One bounded workflow execution with status, limits, timestamps, and initiator |
| `ToolDefinition` | Registered capability with typed input, risk level, timeout, and adapter |
| `ToolCall` | Requested invocation with validated arguments and result metadata |
| `ApprovalRequest` | Explicit user decision for sensitive actions |
| `ExecutionPolicy` | Determines allow, deny, or require-approval before execution |
| `RunEvent` | Append-only trace for model decisions, tool calls, retries, failures, and completion |

Start with deterministic workflows. A model may select from registered tools later, but it must
not execute arbitrary shell commands, URLs, or Python code.

## Staged Delivery

### H1: Minimal Read-Only Boundary

1. Register a typed read-only calendar tool for briefings and meeting preparation.
2. Record tool calls and append-only run events in SQLite.
3. Add per-tool timeouts and tests for denied or malformed calls.
4. Measure whether retrieved context improves secretary workflows.

### H2: Additional Read-Only Integrations

1. Register read-only email adapters.
2. Add allowlisted document and file retrieval.
3. Store source references and privacy classification with retrieved context.

### H3: Approval-Gated Writes

1. Add approval records and a policy service with `allow`, `deny`, and `require_approval`
   outcomes.
2. Add calendar write and email draft tools.
3. Require approval before external writes or sends.
4. Add idempotency keys, run limits, retry controls, and post-action verification.
5. Add compensating actions where APIs support them.

### H4: Specialist Workers

1. Add bounded workflows for research, coding, and drafting.
2. Keep memory writes centralized in secretary-owned services.
3. Require explicit budgets and stop conditions for every worker run.

## Dependency Direction

No new Python package is required for H1. The current `pydantic`, `aiosqlite`, and `httpx`
dependencies are sufficient for typed contracts, SQLite persistence, and HTTP adapters.

Add infrastructure only when a milestone needs it:

- PostgreSQL runtime: a PostgreSQL async driver and `pgvector`.
- Durable background work: a queue after concurrent or multi-device workers become necessary.
- Observability export: OpenTelemetry after local run events are useful and stable.

Avoid installing a general agent framework initially. The core value here is Memocore's policy,
audit, memory, and approval boundary; a framework does not remove the need to design those
contracts.

## Non-Goals

- Free-form autonomous execution.
- Arbitrary code or shell execution.
- Peer-to-peer agent swarms.
- Unbounded model loops.
- Silent external writes.
