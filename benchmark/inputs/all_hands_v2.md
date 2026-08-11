# Agentic Middleware — What We Shipped

Audience: IBM Agentic Middleware org
Slides: 13

Preferences:
- Background: light
- Tone: candid, celebratory but factual

## Cover

Agentic Middleware — What We Shipped, March – May 2026. Department highlights, milestones, and what's next.

## What's Inside

1. Evolving Agents — North Star & Sub-projects
2. CUGA — Goals, Platform & Adoption
3. Sovereign Core — Integration to GA at THINK
4. VAKRA Benchmark — Launch & Impact
5. altk-evolve & RL Breakthroughs
6. IBM Ecosystem & Partners
7. INDIGO Project
8. Publications & Community
9. Key Milestones

## Evolving Agents

North Star: Agents that get more accurate, trustworthy, and cost-effective with every use.

- Memory : Drive continuous improvement via agentic memory. Improve performance across model families. Deliver in CUGA and Sovereign Core. Enhance accuracy, reliability, and safe use.
- Consistency : Improve task success via reasoning tips. +20pp Pass^5 for React and CUGA agents. Integrate via Kaizen. ≥10pp improvement across similar tasks.
- Reinforcement Learning : Iteratively evolve and optimize models. +8-12pp task success on the eval suite. ≥25% inference cost reduction. Self-driven evolution with quality gates.
- Analytics : Debug, improve, and maintain for SLAs. AgMentor in ≥1 IBM product. Repeated use by ≥3 dev teams. ≥5 meaningful issues resolved.

## CUGA — Strategic Goals

North Star: The industry's trusted, enterprise-ready, open-source generalist agent.

1. Enterprise-Ready : Policy adherence, safety, auditability, reliability.
2. Intuitive & Configurable : Developer-friendly, flexible config, enterprise workflows.
3. Industry Credibility : Lead benchmarks for policy-aware, safe agent behavior.
4. External Adoption : Users, contributors, customers, education, and partnerships.
5. Adopted by IBM Software : Architectural alignment, sovereign and enterprise integration.
6. Ecosystem Expansion : Claude Skills, OpenClaw, Confluent, event-driven workloads.

## CUGA — Platform Evolution & Adoption

700+ GitHub stars and growing community engagement.

- New Capabilities : Event Support — Slack events trigger CUGA actions. Knowledge — agents ingest uploaded files and ground responses. Policies — 5 declarative policy types for runtime governance. Episodic Memory — packaged for Bob, Claude, CUGA.
- CUGA Apps & Hackathon : Demo apps collection (travel planner, smart to-dos) laying marketplace groundwork. Hackathon shipped 12+ agents in hours — data scientist, deep research, poster generator. Winning team to be announced.
- ISPF (Z) Agent : Demoed to 12 customers at Z Design Council, validated 3-5 hr/day pain point. Db2 for Z committed CUGA ISPF agent for June 19 GA (code freeze May 1).
- GIDS Conference India : Showcased at IBM booth at India's largest developer conference. Enterprise developers excited about open-source agent harness.
- IBM RPA 30.0.2 GA : CUGA powers computer use and UI automation in IBM RPA. Multi-year collaboration with the RPA team — shipped.

## Sovereign Core — Integration to GA at THINK

April 7 DCUT → May 4 GA.

- Early March — Integration & Bootstrapping : Onboarding requirements aligned for auth, security, observability, compliance. Two-week code-blitz delivered auth/storage, OpenLit observability, UI carbonization. Broker and operator deployment built; validated in mid-sprint playback.
- March – April — On Track & THINK Prep : Delivery on track throughout. Demo scenarios and click-through experience images generated. wxO and Sovereign Core alignment achieved. CUGA imported as a native Lang* agent to wxO.
- Late April — GA Readiness : Episodic memory added just before the GA release. Bug fixes, technical documentation, sales enablement, support hand-over. Demo video produced. Post-THINK workshop planned for May 20 week.
- May 7 — Announced at THINK : Five CUGA-powered prebuilt agents shipped — AI CRM Agent, AI Knowledge Agent, AI Documentation Agent, AI Healthcare Agent, AI General-Purpose Agent. Showcased by Arvind, Dinesh, and Priya on main stages. Customer-accessible at Sovereign Core booth. Joint blog published by Sovereign Core and SIL teams.

## VAKRA Benchmark

Evaluating API and Knowledge Retrieval Agents in enterprise-like settings.

- Launch — March 27 : Tool-grounded, executable benchmark for multi-hop, multi-source enterprise agent evaluation. ~1,000 downloads in first 2 days. Trending Top 20 on HuggingFace, Top 5 for Question Answering datasets. Published via IBM Blog and HuggingFace.
- Analysis Deep-Dive — April 17 : Multi-source reasoning is where performance drops hard. Models diverge — Gemini-3 leans on APIs first, GPT-OSS-120B trusts what it already knows. Policy following still broken across the board.
- Media Pickup — April 20+ : Picked up by Instagram AI channels, The Agent Times, Alpha Maven, Congreso IA 365. ~200 more downloads after the analysis blog. 10+ third-party blogs and videos total.
- Stats since launch : 1,815+ dataset downloads. 1,757 leaderboard visits. 59 GitHub stars. 10+ articles and videos. ~20 downloads/day sustained rate.

## altk-evolve & Reinforcement Learning

- altk-evolve Released — April 10 : Open-source toolkit for evolving agents — agents that learn on the job, one task at a time. No replaying logs, no bloated context. Generalizable guidelines extracted from real work and injected when needed. +8.9 points on goal completion. +14.2 points on the hardest tasks on AppWorld.
- RL Progress — April 3 : AppWorld accuracy moved from 40.5% to 56.5% with GRPO. Prompt optimization had limited benefit. Most promising paths forward: RL training, rubric-based rewards, infrastructure simplification. Next: validate in a client-zero use case.
- Execution Cadence : OKRs, goals, and 2-week sprints defined. First scrum cadence launched March 19. Full sprint/scrum model adopted.
- ALTK Evolution : Restructured for evolving-agents scope. Episodic memory available for Bob, Claude, CUGA. More updates as memory goes public.
- wxO Memory Integration : altk-evolve being tested in the wxO memory subsystem. PoC demonstrated value to the dev team. Next: native wxO integration.

## IBM Ecosystem & Partner Engagement

- wxO Collaboration : Joint exploration on observability and memory gaps; PoC drafted. altk-evolve integration into the wxO memory subsystem. Touchpoints with the wxO CTO on CUGA integration points. Working on a reduced footprint for CUGA inside wxO post-THINK.
- Instana — Agentic Observability : Two capabilities co-created with IT Automation — our first I1 contribution. Tool View — observability into agentic tool calls, errors, execution. Task View — equivalent visibility at the task level.
- AgMentor & ADL Working Group : IT Automation and Middleware aligned on shared PoV and tech-reuse roadmap. Unified demo scenario in progress for end-to-end integration.
- DB2 Team : Deep dive on memory, RL, consistency, and CUGA as a harness. Db2 for Z committed CUGA ISPF agent for June 19 GA.
- Cognos Team : Revisiting their initial negative CUGA decision. Going deeper on applicability.
- Cloud SRE : Building agents and conducting CUGA PoC. Addressing limitations they had faced with wxO.
- IBM RPA : IBM RPA 30.0.2 GA — CUGA powers computer use and UI automation. Multi-year collaboration with the RPA team delivered.

## INDIGO Project

A cross-org initiative to continuously improve agentic systems.

- Vision : Cross-organizational initiative across Merve's and Daby's orgs. Meet business objectives, SLAs, and enterprise policies. Operates across dev and production environments, analyzing intended vs. observed behavior. INDIGO itself operates as an agentic system, autonomously analyzing artifacts and generating recommendations.
- MVP Plan : Three bi-weekly iterations through end of May. Agent builders use INDIGO from the IDE. Improve 3 open-source agents competing on a known benchmark across quality, consistency, policy conformance, reliability, and performance.
- Current Iteration (ends May 5) : Establish the common INDIGO platform. End-to-end improvement cycle with a LangGraph ReAct agent on GAIA Level-1. Agent-driven improvements, semantic feature analytics, basic policy conformance, fault injection, stress testing.
- Related — Project MiRA : Joint initiative across IT Automation, Middleware, and Code for full agent lifecycle and application modernization. MVP being defined.

## Publications, Community & Social

- CAIS 2026 — ACM Conference on AI and Agentic Systems : CUGA demo paper accepted — policy-driven, governed, safe agent execution. ALTK paper accepted.
- Blog Posts Published : VAKRA Benchmark announcement on IBM Blog (March 27). VAKRA analysis deep-dive on HuggingFace Blog (April 17). altk-evolve release on IBM and HuggingFace blogs (April 10). CUGA Policies deep dive (April 30). Sovereign Core agent service joint blog.
- Test & Eval Tooling : Enriched user simulator for benchmark creation, PR raised in AgentOps Core. User stories generated for AskLegal and Concert agents via AgentTune. Claude Cowork plugin and Bob mode for AgentTune.
- Media & Social Reach : VAKRA on Instagram AI channels, The Agent Times, Alpha Maven, Congreso IA. 10+ third-party blogs and videos about VAKRA. Heavy LinkedIn engagement on altk-evolve. CUGA community growing — 700+ GitHub stars.
- Conferences & Events : IBM THINK — Sovereign Core main stages, Arvind, Dinesh, Priya (May 7). GIDS Conference — India's largest dev conference, IBM booth (April 30). Z Design Council — ISPF agent demo to 12 customers (April 3).
- Internal Hackathon — May 2 : 12+ CUGA agents built in hours — data scientist, trip planner, deep research, poster generator. Dashboard of CUGA apps being assembled.

## Key Milestones & Wins

- Sovereign Core GA at THINK : CUGA-powered agent services plus 5 prebuilt agents showcased on the main stage by Arvind, Dinesh, Priya.
- VAKRA Benchmark Launch : 1,815+ downloads, Top 20 HuggingFace dataset, 1,757 leaderboard visits, 10+ third-party articles.
- altk-evolve Open-Source Release : +8.9 goal completion, +14.2 on hardest tasks — agents that learn on the job, one task at a time.
- IBM RPA 30.0.2 GA : CUGA-powered computer use and UI automation. Multi-year collaboration with the RPA team — shipped.
- RL Breakthrough : AppWorld accuracy 40.5% → 56.5% with GRPO. RL pipeline most promising path forward.
- CUGA 700+ GitHub Stars : Strong external adoption. Knowledge feature shipped. ISPF agent committed for Db2 Z June GA.
- 2 Papers at CAIS 2026 : CUGA demo paper plus ALTK paper accepted at the ACM Conference on AI and Agentic Systems.
- Instana Agentic Observability : First I1 contribution — Tool View and Task View for agent observability, co-created with IT Automation.
- INDIGO Project Launched : Cross-org agentic improvement initiative. MVP in three sprints through end of May.
- CUGA Apps Hackathon : 12+ agents built in hours — data scientist, trip planner, deep research. Marketplace groundwork.
- Db2 Z ISPF Agent Committed : watsonx Assistant for Db2 Z includes CUGA ISPF agent, June 19 GA, validated by 12 ZDC customers.
- GIDS Conference Showcase : CUGA showcased at India's largest dev conference. Strong enterprise developer and partner interest.

## Thanks

Agentic Middleware — March to May 2026.
