# Platform Engineering -- Q1 2026 readout

Audience: VPs and directors reviewing platform engineering's Q1 outcomes; the deck is a status readout, not a pitch.

Preferences:
- Tone: factual, measured -- a status readout
- Sub-brand: base IBM
- Length: 6 slides

[[6 slide deck, one slide per source slide, source order preserved]]
[[footer with date and deck name on every body slide]]
[[use accent color sparingly -- one highlight per slide max]]

## Cover
- Eyebrow: PLATFORM ENGINEERING
- Title: Q1 2026 readout
- Subtitle: Where we landed, what slipped, and the Q2 ask
- Author: Maya Okafor, VP Platform Engineering
- Date: April 18, 2026

## The three numbers that summarize Q1 [[render as a multi-stat row]]
- Three headline numbers; each with its Q4 baseline beneath
- **Build p50** -- 2.1 min (down from 4.8 in Q4)
- **Deploy frequency** -- 47 per day (up from 19)
- **Change failure rate** -- 4.2% (down from 7.1%)
- Caption: All three Q1 targets were beat. The first two by a wide margin. [[render as a small italic caption beneath the stat row]]

## What shipped vs what slipped [[render as two stacked panels -- "Shipped" above, "Slipped" below]]
- **Shipped**
  - Build pipeline rewrite
  - Deploy bot v2
  - On-call rotation tooling
  - Secrets rotation API
- **Slipped: Multi-region failover** -- pushed to Q2 because the cert-manager migration took two extra weeks. No customer impact; we held the launch.
- **Slipped: Observability v3** -- partially shipped (logs, traces). Metrics moved to Q2 to share migration window with multi-region failover.

## Incidents in Q1 [[render as a hero stat line above two incident cards]]
- Hero line: **Two SEV-2s. Zero SEV-1s.** [[centered, large; the two cards beneath]]
- **SEV-2 on 2026-02-04** -- Deploy bot regressed on a config-validator change, blocked 11 deploys for 38 minutes. Post-mortem: missing integration test, now in place.
- **SEV-2 on 2026-03-12** -- A noisy-neighbor on the shared build fleet pushed build p99 to 12 minutes for 90 minutes. Fix: per-tenant quotas, shipped 2026-03-19.

## Q2 ask -- three priorities, in order [[render as a numbered priorities list]]
1. **Multi-region failover GA** -- carry-over from Q1.
2. **Observability v3 metrics layer GA**.
3. **Internal developer portal v1** -- one URL for every service's runbook, deploy history, and on-call.

Caption: The Q2 ask matches Q1 headcount. No new hires requested. [[render as a small italic caption beneath the list]]

## Thank you
- Title: Thank you
- Body: Questions welcome
- Contact: maya.okafor@ibm.com
