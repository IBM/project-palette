# Project Heron — H1 2026 review

Audience: engineering leadership + cross-functional reviewers

Preferences:
- Tone: candid, factual, momentum-led
- Length: ~8 slides

Context: Project Heron is a hypothetical internal inference-serving platform.
H1 2026 was its first full half in general availability.

## Cover
Title: "Project Heron". Subtitle: "H1 2026 review — what we shipped, what we learned, what's next." Footer: "Internal · platform engineering · 2026 H1".

## H1 in three numbers
[[render as a three-stat row, side by side, equal weight]]
- **2.4B** inference requests served in H1, +3.7× from December.
- **218ms** p50 latency at end of H1, down from 540ms in January.
- **41%** lower cost per million tokens vs the Q4 baseline.

## The headline result
[[render as a mega-stat, one number dominates]]
Single number: **3.7×**. Below the number, in muted text: "Request volume grew 3.7× in six months while p50 latency dropped 60% and cost per token fell 41%. The capacity and the unit economics moved together — that's the H1 story."

## What we shipped — four product wins
Four product areas crossed major milestones this half. [[render as four comparison cards across]]

- **KV-cache sharing** — Multi-tenant pools share KV-cache across concurrent requests on the same prompt prefix. Shipped in March; alone accounted for 23% of the latency reduction.
- **Speculative decoding** — A small draft model proposes tokens, the large model verifies in batches. Shipped in May for select model families; 1.8× throughput on accepted workloads.
- **Quantized model variants** — INT8 and INT4 variants of the three most-used models, opt-in per tenant. INT8 is the new default; INT4 for cost-sensitive workloads with measured quality regression < 1.5 points.
- **Self-serve onboarding** — Internal teams now spin up a Heron endpoint in under 10 minutes via the platform UI. Cut the time-to-first-token from days to minutes; 38 new teams onboarded in H1.

## Where the team focused — four pillars
Engineering effort across four pillars. [[render as a four-column pillar layout, equal weight]]

- **Latency & throughput (38%)** — KV-cache sharing, speculative decoding, batch tuning, the autoscaler rewrite.
- **Cost (24%)** — Quantization pipeline, INT8/INT4 variant management, capacity rightsizing.
- **Reliability (22%)** — Multi-region failover, graceful degradation, SLO-driven rollouts. Achieved 99.95% in H1, beating the 99.9% target.
- **Developer experience (16%)** — Self-serve onboarding, the platform UI refresh, observability dashboards, the SDK rewrite.

## H1 milestones — eight releases
Eight major releases across the half, ordered chronologically. [[render as a timeline with eight nodes]]

- **Jan 17** — v2.0 GA: streaming responses, first-class long context to 32K.
- **Feb 9** — Autoscaler rewrite shipped; capacity allocation cut from 5min to 30s.
- **Feb 28** — INT8 quantized variants of the three flagship models.
- **Mar 22** — KV-cache sharing GA across multi-tenant pools.
- **Apr 14** — Multi-region failover for the US-East/US-West tier.
- **Apr 30** — Self-serve onboarding UI: from request-form to live in 10 minutes.
- **May 18** — Speculative decoding for the largest model family.
- **Jun 7** — v2.1: long context to 128K, first customer in production on long-context.

## H2 priorities — five themes
The work that defines the next six months. [[render as a bullet list, full-width]]

- **Long-context to 256K** — Finish the chunking work; benchmark against 128K baseline; ship to design partners first.
- **INT4 as a real default** — Push quality-regression below 1 point average; unlock 2.5× cost reduction for general workloads.
- **Cross-region routing** — Latency-aware routing across all three regions; capacity headroom without provisioning the worst case.
- **Cost down another 30%** — Distillation pipeline + KV-cache sharing on the smaller model variants.
- **Platform UI 2.0** — Per-tenant dashboards, cost attribution, latency drill-down. The SDK and UI become the platform's main surfaces.

## Thank you
Title: "Six months of compounding work." Subtitle: "Capacity and cost moved together — let's keep them moving in H2."
