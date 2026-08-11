# watsonx Platform — Q3 FY26 Business Review

Audience: watsonx platform leadership and the division GM
Slides: 14

Preferences:
- Background: light
- Sub-brand: base IBM
- Tone: factual, quantitative, candid

## Cover

watsonx Platform — Q3 FY26 Business Review. Revenue, adoption, margins, and the Q4 plan.

## What's Inside

1. The quarter at a glance
2. Revenue trajectory and the regional split
3. Workload mix and platform economics
4. Customer base and retention
5. The gross-margin walk
6. Industry adoption and the top accounts
7. Pipeline, the Q4 forecast, and risks
8. Q4 priorities

## The Quarter at a Glance

Eight headline metrics, each against last quarter.
- ARR : $487M, up from $431M — +13.0% QoQ, +61% YoY. Sixth straight quarter of double-digit QoQ growth.
- Net revenue retention : 121%, down 3 points from 124% — expansion still strong; two large-account downgrades pulled it down.
- New logos : 94 added, against 78 last quarter — 612 total platform customers.
- Gross margin : 71.4%, up 1.9 points — the inference-cost optimizations are landing.
- Platform uptime : 99.96%, against a 99.95% SLO — met, but with two SEV-1 incidents in the quarter.
- p99 inference latency : 184 ms, down from 232 ms — the serving-stack rework shipped in week 4.
- Active workloads : 41,300 across all customers, up 28% QoQ.
- Cost per 1M tokens served : $0.42, down from $0.51 — a 17.6% unit-cost reduction.

## Revenue Trajectory

ARR across the last eight quarters — the growth curve.
- Q4 FY24 $172M, Q1 FY25 $201M, Q2 FY25 $238M, Q3 FY25 $281M, Q4 FY25 $329M, Q1 FY26 $378M, Q2 FY26 $431M, Q3 FY26 $487M.
- Eight straight quarters of growth; the QoQ rate has held between 11% and 17% the whole way.
- The curve is decelerating slightly in percentage terms — 17% early, 13% now — but the absolute adds are rising: $56M added this quarter, the largest single-quarter dollar add to date.
- The inflection in Q2 FY25 traces to the watsonx.governance bundle going GA.

## Revenue by Region

Q3 ARR split four ways, with year-over-year growth per region.
- Americas : $241M, 49% of ARR, +52% YoY — the anchor, but the slowest-growing region.
- EMEA : $138M, 28% of ARR, +68% YoY — Germany and UK financial-services accounts drove most of the lift.
- Asia-Pacific : $71M, 15% of ARR, +94% YoY — the fastest-growing region; India and Singapore leading.
- Japan : $37M, 8% of ARR, +71% YoY — three large manufacturing accounts signed in the quarter.
- Concentration watch : the Americas share has fallen from 58% to 49% over four quarters — healthy diversification.

## Workload Mix and Platform Economics

What the platform is actually running, by share of compute.
- Inference serving : 47% of platform compute — the dominant and stickiest workload.
- Fine-tuning and adaptation : 21% — up from 14% two quarters ago as customers move past prompting.
- Data preparation and embedding : 18%.
- Governance, evaluation, and monitoring : 9% — small, but the fastest-growing slice.
- Experimentation and other : 5%.
- The economics : cost per 1M tokens fell to $0.42 from $0.51; gross margin is now 76% on inference workloads, 61% on fine-tuning.

## Customer Base and Retention

612 customers — the shape of the base.
- New logos : 94 added, the strongest quarter yet — 41 EMEA, 28 Americas, 17 APAC, 8 Japan.
- By segment : 121 enterprise (>$1M ARR), 318 mid-market, 173 early-stage.
- Net revenue retention : 121% — $44M of expansion, $9M of contraction, $6M of churn.
- Logo churn : 11 customers lost, 1.8% of the base — 7 early-stage, 2 mid-market downgrades, 2 genuine competitive losses.
- Concentration : the top 10 accounts are 23% of ARR — easing from 31% a year ago.

## The Gross-Margin Walk

From $487M of revenue to net contribution.
- Revenue : $487M.
- Less infrastructure and serving COGS : minus $139M, leaving gross profit of $348M — a 71.4% margin.
- Less R&D : minus $121M — the largest cost line, two-thirds of it on model and serving-stack work.
- Less sales and marketing : minus $94M.
- Less general and administrative : minus $38M.
- Net contribution : $95M — a 19.5% net margin, up from 12.1% a year ago.
- The story : the margin expansion is real and driven by the inference unit-cost reduction, not by under-investing in R&D.

## Industry Adoption

ARR, account count, year-over-year growth, and the leading use case per industry.
- Banking and financial services : $164M ARR, 188 accounts, +58% YoY — fraud detection and document intelligence.
- Healthcare and life sciences : $97M ARR, 109 accounts, +81% YoY — clinical-trial document processing.
- Retail and consumer : $71M ARR, 121 accounts, +44% YoY — customer-service agents.
- Public sector : $68M ARR, 74 accounts, +96% YoY — the fastest-growing vertical, citizen-service automation.
- Telecommunications : $52M ARR, 61 accounts, +63% YoY — network operations.
- Manufacturing and other : $35M ARR, 69 accounts, +49% YoY.

## Top Accounts

The eight largest accounts by ARR.
- Meridian Bank — banking — $14.2M ARR — watsonx.ai plus governance — healthy, expanding.
- Atlas Health Network — healthcare — $11.8M ARR — watsonx.ai — healthy.
- Northwind Public Services — public sector — $9.4M ARR — full platform — healthy, the fastest-expanding account.
- Pertex Retail Group — retail — $8.1M ARR — watsonx.ai — watch: usage flat for two quarters.
- Halberd Financial — banking — $7.3M ARR — governance plus watsonx.ai — healthy.
- Tsushima Manufacturing — manufacturing — $6.0M ARR — watsonx.ai — healthy, new this year.
- Lyrebird Telecom — telecommunications — $5.6M ARR — full platform — healthy.
- Crestline Telecom — telecommunications — $5.2M ARR — watsonx.ai — watch: renews in Q4, competitive.

## Pipeline and the Q4 Forecast

What is in front of us.
- Total qualified pipeline : $312M, a coverage ratio of 2.4x against the $130M Q4 net-new target.
- By stage : discovery $118M, evaluation $96M, proposal $61M, contracting $37M.
- The forecast : Q4 ARR projected at $551M (+13.1% QoQ) in the commit case, $568M in the upside case.
- The risk in the number : $48M of the proposal-stage pipeline sits in just 6 accounts — the quarter is lumpy.

## Risks and Watch Items

What could go wrong, named plainly.
- The two large-account downgrades that pulled NRR to 121% both cited cost — a pricing review is underway.
- Crestline Telecom ($5.2M) renews in Q4 against an aggressive competitor — a save play is running.
- p99 latency met the SLO, but two SEV-1 incidents landed in the quarter — reliability investment continues.
- APAC growth is strong but concentrated in four accounts — the region needs diversification.

## Q4 Priorities

Five things for the quarter.
1. Close the $130M net-new target — coverage is there; execution on the 6 lumpy proposal accounts is the risk.
2. Land the pricing review — protect NRR without triggering more cost-driven downgrades.
3. Save the Crestline renewal.
4. Ship the reliability roadmap — zero SEV-1 incidents is the goal.
5. Sustain the inference unit-cost curve — target $0.38 per 1M tokens.

## Closing

watsonx Platform, Q3 FY26 — $487M ARR, a 71% gross margin, 612 customers. The growth is real and the margin story is real; Q4 is about execution and protecting retention.
