# Toward a fault-tolerant logical qubit — IBM Quantum's 2026 milestone
Audience: Research leads, principal engineers, and CTOs evaluating quantum compute for production workloads

Preferences:
- Tone: research-precise, sober, technical
- Sub-brand: IBM Quantum

## Cover
- Quantum · research brief, 2026 milestone, fault-tolerant logical qubit threshold

## Definition — what is a logical qubit?
- Term: Logical qubit
- A logical qubit is an error-corrected information unit encoded across many physical qubits, designed to maintain coherent state long enough to complete a useful computation.
- The encoding uses redundancy + active error correction to detect and fix bit-flip and phase-flip errors mid-computation.
- Industry reference threshold: a logical error rate ≤ 10⁻⁶ per gate is widely treated as the threshold for fault-tolerant operation on practical algorithms.
- Reference: Preskill (2018), "Quantum computing in the NISQ era and beyond"

## What changed in 2026
- (section divider — sets up the data)

## Logical error rate trajectory
- Logical error rate per gate, measured across IBM Heron processor generations from Q1 2023 to Q1 2026
- Five data points: Q1 2023 → 1.2e-3, Q1 2024 → 4.1e-4, Q1 2025 → 8.5e-5, Q3 2025 → 2.3e-5, Q1 2026 → 9.1e-6
- Crossed the 10⁻⁵ threshold in Q3 2025, approaching the 10⁻⁶ fault-tolerant threshold in Q1 2026
- Y axis: log scale, logical error rate per gate; X axis: quarterly timestamps

## Competing error-correction approaches
- 2x2 matrix comparing four approaches on two axes
- X axis: physical-qubit overhead per logical qubit (low → high)
- Y axis: logical error rate floor (high → low)
- Top-right (low overhead, low error): surface code — IBM's current production approach
- Top-left (high overhead, low error): concatenated codes — academic / niche workloads
- Bottom-right (low overhead, high error): bare physical qubits — NISQ-era baseline
- Bottom-left (high overhead, high error): repetition codes — pedagogical only

## In their words
- Quote: "This is the first time we've seen a logical error rate decreasing faster than the physical qubit count is increasing — that's the signal we were watching for."
- Attribution: Dr. Elena Marchetti, Senior Research Lead, IBM Quantum
- No avatar — clean text-only pull quote

## What's next
- Early-access program for Heron R3 fleet opening Q2 2026
- Partner with IBM Quantum Network to scope a candidate workload
- Pricing and provisioning TBA at IBM Think 2026
