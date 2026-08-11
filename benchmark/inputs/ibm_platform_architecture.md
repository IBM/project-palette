# watsonx Orchestrate — Enterprise Reference Architecture

Audience: IBM enterprise architects and the platform engineering org
Slides: 13

Preferences:
- Background: light
- Sub-brand: base IBM
- Tone: precise, technical, structured

## Cover

watsonx Orchestrate — Enterprise Reference Architecture. The layers, the request path, the governance model, and the rollout.

## The Drivers

What the architecture must satisfy — functional and non-functional.
- Functional : eight core business processes delivered as agentic workflows in year one, roughly thirty by year three. Agents call systems of record — CRM, ERP, the core platform — only through governed connectors, never directly. Every high-risk action routes to a human-in-the-loop queue before it executes. Multi-channel from day one: web, mobile, conversational chat and voice, and an agent-assist console.
- Non-functional : 99.95% platform availability, with no single layer a point of failure. p95 latency under 2 seconds for an agent response, channel to channel. SOC 2 Type II, in-region data residency, and a complete immutable audit trail. Horizontal scale to 500 concurrent sessions at launch, 5,000 by year three. Portable across hybrid cloud — no lock-in to one provider's managed services.

## The Architecture at a Glance

Five layers, twenty components. A request enters at the top and resolves downward — each layer talks only to the layer directly below it.
- Experience layer : the web and mobile apps, the conversational channels (chat and voice), the agent-assist console, and the public API gateway.
- Orchestration layer : the agent orchestrator, the workflow engine, the policy and guardrail service, and the human-in-the-loop queue.
- Agent and model layer : the task agents, the tool and connector registry, foundation models on watsonx.ai, and the retrieval service over a vector store.
- Data and integration layer : the enterprise data fabric, the systems-of-record connectors, the event stream, and the document store.
- Foundation layer : Red Hat OpenShift, identity and access management, observability via Instana, and security and key management.

## The Request Lifecycle

Eight steps from a user request to a completed action — each with its latency budget.
1. The channel receives the request; the API gateway authenticates, rate-limits, and forwards it — 80 ms.
2. The orchestrator classifies intent and selects the workflow — 120 ms.
3. The workflow engine resolves the next task and dispatches it to a task agent — 60 ms.
4. The task agent retrieves grounding from the vector store and reference data through connectors — 340 ms.
5. The agent calls a foundation model on watsonx.ai for the reasoning step — 900 ms, the dominant cost.
6. The policy service evaluates the proposed action against the guardrail rules — 90 ms.
7. A high-risk action is parked in the human-in-the-loop queue; a low-risk action proceeds straight through — async, or 0 ms.
8. The action executes through a governed connector and the result returns to the channel — 210 ms.
- The p95 budget : 1.8 seconds of compute against the 2-second target, leaving 200 ms of network headroom.

## The Orchestration Layer, Decomposed

The load-bearing layer — four components.
- The agent orchestrator : owns intent classification, workflow selection, and conversation state. The single component every request passes through.
- The workflow engine : executes the multi-step workflow definition; handles retries, compensating actions, and branching. State is durable.
- The policy and guardrail service : evaluates every proposed action against declarative policy. Five policy types — data access, action authorization, content safety, rate, and cost.
- The human-in-the-loop queue : parks high-risk actions for review with full context, an approve / modify / reject decision, and an SLA timer.

## Data and Integration

How the platform reaches the enterprise's systems.
- The enterprise data fabric : the governed catalog — every dataset has an owner, a classification, and lineage. Agents see only what policy permits.
- The systems-of-record connectors : governed adapters to CRM, ERP, the core banking or claims platform, and the ticketing system. Connectors enforce field-level access; agents never hold raw credentials.
- The event stream : agent actions, tool calls, and outcomes are emitted as immutable events, for audit and analytics.
- The document store : the source documents for retrieval, partitioned by classification, with the vector index built over it.

## Governance and Guardrails

Who reviews what — the governance model across the action lifecycle, role by role.
- The agent : proposes an action, with its reasoning trace and the data it drew on.
- The policy service : evaluates automatically — a clean low-risk action proceeds; anything flagged escalates.
- The human reviewer : for high-risk actions, sees the full context and decides approve / modify / reject within the SLA.
- The audit service : records the proposal, the policy decision, the human decision, and the outcome — immutable and queryable.
- The four gates every action passes : a data-access check, an authorization check, a safety check, and a cost check.

## The Security Model

Zero-trust across the layers.
- Identity : every request, every agent, and every connector call is authenticated — no trust granted by network location.
- Least privilege : agents hold scoped, time-bound credentials; the connector enforces field-level access.
- Isolation : per-tenant data isolation, with in-region residency enforced at both the storage and processing layers.
- Auditability : the immutable event stream is the system of record for who did what.
- Assume breach : secrets rotate automatically; the blast radius of a compromised agent is one tenant, one scope, one session.

## Deployment Topology

Three deployment options, compared on what matters to the buyer.
- SaaS : IBM-managed on IBM Cloud. Fastest to value (weeks), the lowest operating burden, the least control over data location. Fits the mid-market.
- Hybrid : the control plane IBM-managed, the data plane in the customer's OpenShift. Data stays in the customer's environment; a moderate operating burden. Fits the regulated enterprise.
- On-premises : fully customer-operated on their own OpenShift. Maximum control and residency, the highest operating burden, the slowest to value. Fits the most regulated accounts.
- The recommendation : hybrid as the default for enterprise — it satisfies residency without the full on-prem operating cost.

## Capacity and SLAs

The numbers the architecture commits to.
- Availability : 99.95% — roughly 22 minutes of allowed downtime per month.
- Latency : p95 agent response under 2 seconds; p99 under 3.5 seconds.
- Scale : 500 concurrent sessions at launch, 5,000 by year three — horizontal at every layer.
- Throughput : 40 agent actions per second sustained at launch.
- Recovery : a 15-minute RTO and a 5-minute RPO, with automatic cross-zone failover.

## The Rollout Roadmap

Four quarters from foundation to scale.
- Q1 : stand up the foundation and orchestration layers; two pilot workflows; SaaS only.
- Q2 : add the governance and human-in-the-loop layer; hybrid deployment available; eight workflows live.
- Q3 : the full connector set; the voice channel; the SOC 2 Type II audit completed.
- Q4 : scale to 5,000-session capacity; the on-prem option available; roughly twenty workflows live.

## Risks

What could derail the rollout.
- The foundation-model step (900 ms) dominates the latency budget — a model regression would blow the p95 SLA.
- The connector set is the integration long pole — every system of record is a bespoke effort.
- Human-in-the-loop throughput : if review SLAs slip, the queue becomes the bottleneck rather than the safeguard.
- Hybrid deployment shifts operating burden to the customer's platform team — adoption depends on their OpenShift maturity.

## Closing

watsonx Orchestrate — five layers, a two-second budget, governed end to end. The architecture is sound; the rollout risk is the connectors and the human-review throughput.
