# Retrieval-Augmented Generation — a practical introduction

Audience: engineers, product folks, and technical leaders working with LLMs

Preferences:
- Tone: clear, teacherly, concrete examples over abstractions
- Length: ~8 slides

## Cover
Title: "Retrieval-Augmented Generation". Subtitle: "How modern LLM applications stay accurate, current, and grounded." Footer: "A practical introduction · 2026".

## LLMs alone vs LLMs with RAG
The shift in what an LLM can reliably do once retrieval is layered in. [[render as a before/after, two equal panels side by side]]

**Left — LLM alone:**
- Knows only what it was trained on; cuts off at a fixed date.
- Hallucinates plausible-sounding facts when asked about specifics.
- No way to cite sources or show provenance.
- Updating its knowledge requires re-training or fine-tuning.

**Right — LLM + RAG:**
- Pulls fresh, domain-specific content at query time from your own corpus.
- Grounds every answer in retrieved evidence — measurably fewer hallucinations.
- Returns citations alongside the answer; users can verify claims.
- Updating its knowledge is a re-index, not a re-train. Hours, not weeks.

## What is RAG?
Definition slide. [[render as a two-pane definition layout]]

**Term:** RAG — Retrieval-Augmented Generation.

**Body:** A pattern for building LLM applications where, at query time, the system first *retrieves* a small set of relevant documents from a knowledge store, then *augments* the LLM's prompt with those documents as context, then asks the LLM to *generate* an answer grounded in that context. The LLM stays small and general; the knowledge stays large, fresh, and outside the model. The retrieved evidence travels with every answer, making citations natural. RAG turns "what does the model know" into "what does the model see" — a problem you can solve with a better index.

## How RAG works — three steps
Three steps execute on every query. [[render as a three-column pillar layout, equal weight]]

- **Retrieve** — The user's question is encoded as a vector. The vector store returns the top-K nearest chunks of your corpus by semantic similarity. Typical K is 5 to 20; latency target is under 100ms.
- **Augment** — The retrieved chunks are concatenated into a prompt template alongside the original question, plus instructions like "answer using only the provided context." This prompt is what actually reaches the LLM.
- **Generate** — The LLM produces an answer grounded in the augmented context, with citations back to the source chunks. The answer is returned to the user; failure modes (no chunks found, low confidence) can short-circuit before generation.

## Three quality dimensions you have to measure
RAG quality decomposes into three measurable axes. [[render as three comparison cards across]]

- **Retrieval quality** — Did the right chunks come back? Measure recall@K against a labeled query set; the upper bound on the system. If the retriever misses the answer, no LLM can recover it.
- **Faithfulness** — Did the answer use only the retrieved chunks, or did the LLM hallucinate beyond them? Measured by LLM-as-judge or a citation-verification pass. Faithfulness gates trust.
- **End-to-end accuracy** — Did the user get a correct, complete answer? The composite metric users care about. Improves when *both* retrieval and faithfulness improve — but not before.

## The number that matters
[[render as a mega-stat, one number dominates]]
Single number: **71%**. Below the number, in muted text: "The average reduction in hallucinated facts when grounded RAG with citations replaces an unconstrained LLM response, measured across the Stanford HELM benchmark suite. The catch: that reduction *requires* a faithfulness pass — without it, the gain is closer to 28%."

## When RAG is the wrong tool
RAG fits a real but bounded problem space. Reach for something else when: [[render as a bullet list, full-width]]

- **The knowledge is small, stable, and general** — fine-tuning is simpler and faster at query time.
- **The task is structured (classification, extraction, routing)** — a smaller specialized model beats RAG on cost and latency.
- **Responses must be deterministic** — RAG introduces variability in retrieved context; structured queries against the underlying data may be more appropriate.
- **The corpus is small enough to fit in the context window** — skip the retrieval step entirely, dump it into the prompt.
- **Real-time decisions matter** — sub-50ms latency budgets are hard to hit when retrieval, generation, and a faithfulness check all run sequentially.

## What to remember
[[render as a key-takeaway box]]
Three things: retrieval quality sets the ceiling, faithfulness checks gate the trust, and RAG is the right answer when your corpus is large, fresh, or domain-specific — not when it isn't.
