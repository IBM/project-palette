# CUGA Hackathon Kick off!

Audience: IBM SIL · CUGA
Slides: 14

Preferences:
- Background: light
- Tone: candid, energetic, practical

## Cover

CUGA Hackathon Kick off! — Build with CUGA! Three days. Eat your own dog food. IBM SIL · CUGA · May 5 – 11.

## Why join?

A 3-day sprint to ideate, build, and showcase what's possible with CUGA & CUGA Apps.
- Get hands-on with CUGA : Build something interesting — and learn the platform by using it.
- Collaborate across teams : Pair with peers from different teams. Cross-pollination is the point.
- Ship something real : From idea to working demo in just a few hours.
- Learn what wins (and win prizes!) : Get clear signal on what makes a winning submission.

## Three days + showcase

May 5 → May 11 · Winner announced May 13.
- Day 1 — May 5, 8:45 AM ET, 45 min — Kick Off & Team Formation : Intro to CUGA / Apps / Skills. Hackathon format & goals. Team formation. Winning criteria & prizes.
- Day 2 — May 6, 9:00 AM ET, 2 hr — Build Day : Hands-on coding & building. Experiment, prototype, iterate. Drop into the virtual session if stuck.
- Day 3 — May 7, 2:30 PM US, 2 hr — Q&A & Submission Support : Continue building. Help with issues. Last-mile polish. Other times for the Israel team.
- Showcase — May 11, 9:00 AM ET, 2 min/team — Demos & Winners : You get 2 minutes. Live demo preferred. Winner announced May 13 at All Hands.

## Get CUGA Running in 60 Seconds

Prereqs: Python 3.12+, uv.

```bash
git clone https://github.com/cuga-project/cuga-agent.git
cd cuga-agent
uv venv --python=3.12 && source .venv/bin/activate
uv sync
```

Configure .env — pick ONE inference provider and set its API key:

```bash
# Choose ONE provider:
OPENAI_API_KEY=sk-...                          # OpenAI
WATSONX_API_KEY=...   WATSONX_PROJECT_ID=...    # IBM WatsonX
AZURE_OPENAI_API_KEY=...   AZURE_OPENAI_ENDPOINT=...
GROQ_API_KEY=...                               # Groq
OPENROUTER_API_KEY=...                         # OpenRouter
MODEL_NAME=...                                 # All need MODEL_NAME
LANGFUSE_SECRET_KEY="..."                      # Observability
LANGFUSE_PUBLIC_KEY="..."
LANGFUSE_HOST="https://us.cloud.langfuse.com"
```

Then: cuga start demo_crm → open https://localhost:7860
Repo: github.com/cuga-project/cuga-agent

## SDK — Drop CUGA Into Your Code

Bring your own LangChain tools; await agent.invoke(...) returns a structured result.

```python
from cuga import CugaAgent
from langchain_core.tools import tool
import asyncio

@tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

agent = CugaAgent(tools=[add_numbers])

async def main():
    result = await agent.invoke("What is 5 plus 3?")
    print(result.answer)

asyncio.run(main())
```

Source: README.md · Same agent runtime as the UI demos — just embedded.

## Four Ways to Instantiate CUGA

Pick the approach that fits your use case — same agent core under the hood.
- SDK : Embed CUGA inside your Python app. Launch — from cuga import CugaAgent.
- UI : Chat interface for demos & exploration. Launch — cuga start demo_crm → localhost:7860.
- YAML : Declarative multi-agent supervisor. Launch — cuga start demo_supervisor.
- CLI/REST : Scripts & integrations via a FastAPI server. Launch — FastAPI included with cuga start.

YAML example: src/cuga/backend/tools_env/registry/config/supervisor_demo_crm.yaml

## Skills — Reusable Agent Instruction Packs

CUGA implements Anthropic-style Agent Skills (SKILL.md files).
- Discovered from .cuga/skills/ and ~/.config/cuga/skills/
- Loaded on-demand via the load_skill tool — keeps the system prompt lean.
- Files: src/cuga/backend/skills/loader.py, registry.py

Get started with: `cuga start demo_skills`

Example .cuga/skills/pptx/SKILL.md:

```
---
name: PPTX Generator
description: Create PowerPoint presentations
requirements: ["python-pptx"]
---
# Step 1: ...
```

Branch preview — currently on feat/skills-support · git checkout feat/skills-support to try it.

## Policies — Agent Guardrails

Five policy types · trigger by keyword, NLP, app, or agent state: Intent Guard, Playbook, Tool Approval, Tool Guide, Output Formatter.

Get started with an example: `cuga start demo_health`

SDK example:

```python
await agent.policies.add_intent_guard(
    name="Block Deletions",
    keywords=["delete", "remove"],
    response="Deletion not permitted.",
)

await agent.policies.add_tool_approval(
    name="Approve Transfers",
    required_tools=["transfer_funds"],
)
```

Files: src/cuga/backend/cuga_graph/policy/{models, configurable, enactment}.py

## Three Demos to Fork From

Run cuga start <name> · full list: uv run cuga start --help
- cuga start demo_crm — Single-agent CRM workflow : CRM API (16k+ records) + email MCP + filesystem MCP + SMTP sink. Handles filter contacts by revenue, find opportunities by region, draft templated emails. Killer 30s — file to CRM filter to revenue calc to email, chained in one prompt.
- cuga start demo_supervisor — Multi-agent supervisor (YAML-driven) : CugaSupervisor orchestrates 3 specialists (CRM, Email, Filesystem) via the A2A HTTP protocol. Coordinator on Groq GPT-OSS 120B. Killer 30s — one query broken into sub-tasks, delegated, aggregated. Visible coordination.
- cuga start demo_knowledge — RAG over your documents : Ingests Markdown (sample cuga_knowledge.md included). RAG engine, 4 quality profiles (speed to max_quality), fastembed embeddings, local vector DB. Killer 30s — drop a doc, ask a question, get a synthesized answer with citations, no retraining.

Pick one as your starting point — fork, swap tools, ship.

## Logistics

- Slack : #cuga-hackathon — the primary channel.
- Registration : Sign up with name, team, and target use case. ibm.box.com/notes/2216271410392
- Teaming : Form teams of 1 to 3. Cross-team collaboration encouraged.
- What to deliver : Working code into cuga / cuga-apps, plus a 2-minute demo.
- Showcase : End-of-event demos and pitches — live preferred.

## What to build

Anything that uses CUGA — grounded by a real use case.
- T-01 CUGA Skills : Reusable agent skills you can package and share.
- T-02 Interesting Tools : New MCP tools. Fresh capabilities the agent can call.
- T-03 CUGA Apps : End-to-end applications powered by CUGA.
- T-04 Features in CUGA : Improvements to the agent itself — perception, planning, memory.
- T-05 Anything using-CUGA! : Eat your own dog food. Use CUGA to build things.

"Eat your own dog food."

## The hackathon flow — before, during, after

Three phases, from prep to merged code.
- Before — show up ready : Set up CUGA and CUGA Apps and poke around. Brainstorm where an agent can help, and bring concrete ideas. Form a team of 1 to 3, cross-team encouraged.
- During — build, collaborate, learn : Pair up and code together; lean on Claude / Bob; ask in #cuga-hackathon when stuck. Branch off main and push early. Ship something that works, and document the gaps you hit as you go.
- After — land the work : Pick one team rep to own the merge. Open scoped, reviewable PRs — cuga first, then cuga-apps. Mind dependencies: a cuga-apps PR that needs cuga changes waits for cuga to merge first.
- Repos : cuga — github.com/cuga-project/cuga-agent · cuga-apps — github.com/cuga-project/cuga-apps/tree/apps

## Judging and the 2-minute pitch

Three judges, three equally-weighted criteria — and a hard 2-minute cut-off per team, no exceptions.
- Judged on three criteria, weighted equally : Agency & Reasoning — "The Brain". Execution & Integration — "The Hands". Impact & Visibility — "The Point".
- Your 2 minutes — cover four things:
  - What you built — demo it, live demo encouraged.
  - Learnings, gaps, feature ideas, and bugs the experience surfaced.
  - Why it needs an agent — what is CUGA's role, and why is this interesting.
  - How ready it is — from rough prototype to production-ready.

## Questions?

Let's build & have fun. Questions?
Links — Slack: #cuga-hackathon. cuga: github.com/cuga-project/cuga-agent. cuga-apps: github.com/cuga-project/cuga-apps/tree/apps. Box: ibm.ent.box.com/notes/2216271410392.
Contacts — Anu Murthi (@anupama.murthi), Jim Laredo (@laredoj), Harold Ship (@harold), Sami Marreed (@sami).
