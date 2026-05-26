# The incident-response playbook

Audience: the engineering organization — every on-call engineer, plus the leads who own incident response. The playbook is what someone reads at 2 a.m. and what a new joiner reads in week one.
This is the full playbook brief. The deck pulls the operational core; the brief carries more of the detail and the edge cases than six slides need.

Preferences:
- Tone: clear, operational, calm — a playbook is read under pressure, so it is plain and unambiguous.
- Sub-brand: base IBM.
- Length: six slides — cover, the lifecycle, the swimlane, the severity tiers, escalation and comms, the review.

## Cover
- The incident-response playbook.
- Subtitle: the five stages, who runs them, and how an incident is escalated, communicated, and closed.
- Scope: the lifecycle, the role swimlane, the severity tiers, the escalation ladder.

## The lifecycle — five stages
Every incident, large or small, runs the same five stages in order.
- Detect. An alert fires, a customer reports, or a deploy goes wrong. The on-call engineer acknowledges within five minutes and confirms it is a real incident.
- Triage. Assess scope and impact, declare a severity, and — for SEV1 and SEV2 — open an incident channel and page an incident commander.
- Mitigate. Stop the bleeding. Restore service by the fastest safe route — roll back, fail over, disable the feature. Mitigation is not the fix; it is the end of customer impact.
- Resolve. With impact stopped, find and fix the root cause, verify the fix, and formally close the incident.
- Learn. Within five business days, run a blameless review and turn its findings into tracked action items.
The line that matters: Mitigate before Resolve. Stopping customer impact always comes before understanding why — the two are different jobs and the playbook never lets the second delay the first.

## The swimlane — who does what, stage by stage
Four roles, five stages, every cell an action — the swimlane is the playbook's core.
- Incident Commander. Detect: not yet engaged. Triage: paged for SEV1/SEV2, takes command, owns the call. Mitigate: directs the response, decides the mitigation, holds the timeline. Resolve: confirms service restored, hands to root-cause owner. Learn: schedules and runs the review.
- On-call engineer. Detect: acknowledges the alert, confirms a real incident. Triage: assesses scope, proposes a severity. Mitigate: executes the mitigation under the commander's direction. Resolve: drives root cause and the fix. Learn: writes the incident timeline.
- Communications lead. Detect: not engaged. Triage: engaged for SEV1/SEV2, drafts the first status update. Mitigate: posts updates on the fixed cadence. Resolve: posts the all-clear. Learn: drafts the external write-up if one is owed.
- Subject experts. Detect: not engaged. Triage: pulled in as the affected system is identified. Mitigate: advise on the safe mitigation for their system. Resolve: own the fix in their area. Learn: contribute their part of the timeline.
The rule the swimlane enforces: every stage has exactly one role that owns the decision — the commander once engaged, the on-call engineer before that. No stage is leaderless.

## The severity tiers — how impact is graded
Severity is declared at Triage and sets everything downstream. Three tiers.
- SEV1 — critical. Major customer-facing outage or data risk. Response immediately, 24/7. Incident commander and comms lead paged. Executive notified. Updates every 30 minutes.
- SEV2 — major. Significant degradation, a workaround exists, or a subset of customers affected. Response immediately during business hours, within 30 minutes otherwise. Incident commander paged. Updates every 60 minutes.
- SEV3 — minor. Limited impact, no customer escalation, can wait for business hours. Handled by the on-call engineer; no commander required. Updates at resolution.
Severity is not fixed — it is re-assessed as scope becomes clear, and the playbook expects it to be raised or lowered mid-incident. The rule: when in doubt, declare the higher severity. It is cheap to stand down and expensive to be late.

## Escalation and communication — when to pull the cord
Two ladders run in parallel through every SEV1 and SEV2.
- The escalation ladder. 0 minutes: on-call engineer. 15 minutes without mitigation: page the incident commander and the system's subject expert. 45 minutes: page the engineering director. 90 minutes: page the VP and convene a leadership bridge. Escalation is automatic on the clock — it does not wait for someone to ask.
- The communication cadence. The comms lead posts a first update within 15 minutes of declaration, then every 30 minutes for SEV1 and every 60 for SEV2 — even when the update is "no change". Internal status channel always; the public status page for any customer-facing SEV1. Silence is the failure mode the cadence exists to prevent.
The principle under both: escalation and communication run on a clock, not on judgment. Under pressure, judgment is the thing that slips.

## The review — closing the incident well
The incident is not closed when service is restored; it is closed when the review is done.
- Timing. Within five business days, while memory is fresh.
- Blameless. The review examines the system and the process, never the person. The question is always "what made this failure easy", never "who erred".
- Output. A timeline, a root-cause analysis, and a set of tracked action items each with an owner and a due date. An action item with no owner is not an action item.
- The test of a good review. It produces changes that would have prevented or shortened this incident — and those changes ship. A review that produces only a document has failed.

