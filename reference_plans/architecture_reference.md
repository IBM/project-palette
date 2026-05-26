# Enterprise agentic AI platform — reference architecture

Audience: enterprise architects and technical decision-makers evaluating an agentic AI platform for a large organization.
This is the full architecture working brief — every layer, component, connection, and decision is here. The deck pulls from it; it should not try to render all of it.

Preferences:
- Tone: precise, technical, decision-oriented — architects read for the components and the connections, not the adjectives.
- Sub-brand: base IBM.
- Length: eight slides — cover, the drivers, the architecture at a glance, the orchestrator decomposed, the request flow, cross-cutting concerns, the key decisions, the roadmap.

## Cover
- Reference architecture, 2026 — an enterprise agentic AI platform on IBM Cloud.
- Subtitle: one governed, observable platform for agentic automation across the enterprise — layer by layer.
- Scope: five layers, twenty components, four load-bearing decisions.

## The drivers — what the architecture must satisfy
Functional requirements:
- Support eight core business processes as agentic workflows in year one, roughly thirty by year three.
- Agents must call enterprise systems of record — CRM, ERP, the core platform — through governed connectors, never directly.
- Every high-risk action — a payment, a contract change, a customer-data write — routes to a human-in-the-loop queue before execution.
- Multi-channel: web, mobile, conversational (chat and voice), and an agent-assist console for human staff.
Non-functional requirements:
- 99.9% platform availability; no single layer is a single point of failure.
- p95 latency under 2 seconds for an agent response, measured channel to channel.
- SOC 2 Type II; data residency held in-region; a complete, immutable audit trail of every agent action.
- Horizontal scale to 500 concurrent agent sessions at launch, 5,000 by year three.
- Portable across hybrid cloud — no hard dependency on a single provider's managed services.

## The architecture at a glance — five layers, twenty components
The platform is five layers. A request enters at the top and resolves downward; every layer talks only to the layer directly below it, through a published contract.

Layer 1 — Experience layer (where users and channels connect):
- Web and mobile apps
- Conversational channels — chat and voice
- Agent-assist console — for human staff handling escalations
- Public API gateway — authenticates and rate-limits every inbound request

Layer 2 — Orchestration layer (the control plane):
- Agent orchestrator — routes a request to a plan and the agents that fulfil it
- Workflow engine — runs the long-running, multi-step business processes
- Policy and guardrail service — checks every action against policy before it executes
- Human-in-the-loop queue — holds high-risk actions for human approval

Layer 3 — Agent and model layer (the intelligence):
- Task agents — a managed pool, one or more per business process
- Tool and connector registry — the catalog of actions agents are allowed to call
- Foundation models — served by watsonx.ai
- Retrieval service — a managed vector store over enterprise knowledge

Layer 4 — Data and integration layer:
- Enterprise data fabric — a governed access layer over enterprise data
- Systems-of-record connectors — CRM, ERP, the core platform
- Event stream — the asynchronous backbone between services
- Document store — source documents for retrieval and audit

Layer 5 — Foundation layer (IBM Cloud):
- Container platform — Red Hat OpenShift
- Identity and access management
- Observability — Instana, with traces across every layer
- Security and key management

Key cross-layer connections: the API gateway hands every request to the agent orchestrator; the orchestrator calls the policy service before any action and the workflow engine for multi-step processes; task agents draw on foundation models and the retrieval service, and act only through the tool registry; the tool registry reaches systems of record only through the data fabric's governed connectors; every layer emits traces to Instana and authenticates through IAM.

## The orchestrator, decomposed
The agent orchestrator is the load-bearing component, so the deck zooms into it. Five sub-components, in the order a request passes through them:
- Intent router — classifies the inbound request and selects the business process.
- Plan builder — turns the intent into an ordered plan of steps.
- Agent dispatcher — assigns each step to a task agent and a tool.
- State manager — holds the run state so a long process can pause for human approval and resume.
- Result aggregator — assembles step results into a single response and hands it back up.
The policy and guardrail service is consulted by the dispatcher before each step and by the aggregator before the response is released.

## The request flow — channel to channel
A single request, traced through the platform:
1. The request enters at a channel and hits the public API gateway — authenticated, rate-limited.
2. The gateway forwards it to the agent orchestrator; the intent router classifies it and picks the business process.
3. The plan builder produces an ordered plan; the workflow engine takes ownership if the process is long-running.
4. For each step, the agent dispatcher assigns a task agent — the agent calls a foundation model to reason and the retrieval service for grounding.
5. To act, the agent calls a tool from the registry; the tool reaches a system of record only through a governed data-fabric connector.
6. Before any high-risk action executes, the policy service routes it to the human-in-the-loop queue for approval.
7. The result aggregator assembles the response; the policy service clears it; the gateway returns it to the channel.
Every step emits a trace span to Instana, so the whole flow is one observable transaction.

## Cross-cutting concerns — applied to every layer
Three concerns are not a layer; they cut across all five:
- Security — zero-trust between layers, mutual TLS on every inter-layer call, secrets and keys in the key-management service; no component trusts another by network position.
- Governance — the policy service enforces action policy at runtime; watsonx.governance tracks every model's lineage and approvals; the audit trail is immutable and complete.
- Observability — Instana traces every request across all five layers; agent quality metrics, groundedness and task success, are evaluated continuously, not just at build time.

## The key architecture decisions
Four load-bearing decisions, each with the alternative that was considered and rejected:
- Red Hat OpenShift as the container platform. Why: portability across hybrid cloud, no lock-in to one provider's managed Kubernetes. Considered and rejected: a single cloud's managed Kubernetes — faster to stand up, but it fails the portability requirement.
- A central agent orchestrator, not peer-to-peer agents. Why: one place to enforce policy, one trace per request. Considered and rejected: choreographed peer-to-peer agents — more scalable in theory, but governance and observability become near-impossible.
- Retrieval over a managed vector store, not per-domain fine-tuning. Why: knowledge stays fresh without retraining, and cost scales with use. Considered and rejected: fine-tuning a model per domain — a higher accuracy ceiling, but stale the day the knowledge changes.
- A human-in-the-loop queue for high-risk actions. Why: the compliance requirement is non-negotiable. Considered and rejected: full autonomy with post-hoc audit — lower latency, but unacceptable for payments and contract changes.

## The roadmap
Three phases:
- Phase 1 (Q1-Q2) — stand up the foundation and orchestration layers; two pilot agents on two business processes.
- Phase 2 (Q3-Q4) — the full data and integration layer; scale to ten agents; the human-in-the-loop queue in production.
- Phase 3 (next year) — multi-region for residency and availability; governance hardening; open the platform to all thirty business processes.

