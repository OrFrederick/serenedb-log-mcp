# LogSearcher

Turns large log corpora into role-specific answers: the database searches and calculates, an AI agent explains the returned evidence to an engineer, a team lead or a manager.

## Audience

**Role**:
One of three askers with distinct questions, evidence needs and decisions: Engineer, Team Lead, Manager.
_Avoid_: Persona, user type

**Engineer**:
The Role that investigates one failure: what failed, where, and what happened just before and after.

**Team Lead**:
The Role that looks for recurring problems over a stated period and which Components and Teams they involve.

**Manager**:
The Role that decides which observed problems deserve attention first, using measured frequency and affected Components only.

## Data

**Base Corpus**:
The BGL and OpenStack datasets as loaded for the core challenge.
_Avoid_: Dataset (when meaning both), the logs

**Dataset**:
One named log source inside or outside the Base Corpus (e.g. BGL, OpenStack, OTel demo).

**Log Entry**:
One line of one Dataset, after parsing.
_Avoid_: Event, record, incident

**Component**:
The emitting subsystem named in a Log Entry (e.g. BGL `KERNEL`, OpenStack `nova.compute.manager`).
_Avoid_: Service, module

**Team**:
The owning group a Component maps to via the Ownership Table.
_Avoid_: Owner, squad

**Ownership Table**:
The mapping from Component to Team, supplied by the organisers.

**Data Scope**:
The Datasets, time range and filters a calculation covered.

## Evidence

**Evidence**:
The result of one executed tool call: its query, parameters, Data Scope, counts and returned rows.
_Avoid_: Proof, source, citation

**Evidence Ledger**:
The append-only log of all Evidence produced in an investigation, addressable by evidence id.

**Truncated**:
Said of Evidence whose returned rows are fewer than the total matching Log Entries.

**Observed Fact**:
A statement directly supported by cited Evidence.
_Avoid_: Finding (ambiguous)

**Hypothesis**:
A possible explanation the Evidence is consistent with but does not establish.
_Avoid_: Root cause (unless established)

**Unknown**:
Something the logs cannot establish, stated explicitly.

**Next Step**:
The Role-appropriate action recommended from the Observed Facts: a check for the Engineer, an investigation priority for the Team Lead, a prioritised action for the Manager.
_Avoid_: Recommendation (when unscoped), fix

## Proposed terms (overnight, unconfirmed by humans)

Proposed by the overnight Claudes from issue #1 (Incident model, consensus) and issue #6 (template mining, not yet consensus). They are used in `docs/plan.md`. The humans must confirm them before they move into the sections above.

**Pattern**:
The message shape that Log Entries share once variable parts (ids, numbers, addresses) are masked. For BGL alert lines it is the alert category; otherwise it is the template id assigned in the database. Its text is the template text.
_Avoid_: Template (for the unit itself), cluster, signature

**Pattern Family**:
Patterns whose template texts differ only by a known wording drift, such as a trailing "over N seconds", merged so that a trend does not break when the wording changes.
_Avoid_: Template group

**Burst**:
A maximal chain of Log Entries of one Pattern on one source (a BGL node or an OpenStack instance), where each entry follows the previous one within the gap threshold.
_Avoid_: Spike, storm (as a unit)

**Incident**:
A maximal chain of Log Entries of one Pattern, merged across sources by default, where each entry follows the previous one within the gap threshold (300 s by default). Counted separately from Log Entries; matching Log Entries are never Incidents by themselves.
_Avoid_: Event, alert, issue

**Spread**:
How far an Incident reaches: its distinct nodes, node cards, midplanes and racks (instances or hosts for OpenStack), plus the share of its Log Entries on the top node.
_Avoid_: Blast radius (in measured statements)

**Shape**:
The label that classifies an Incident by Spread: single-node storm, single-node isolated, one midplane, multi-midplane or machine-wide.
_Avoid_: Type, kind

**Recurrence**:
How often a Pattern produces Incidents over a stated period: active weeks, first seen and weekly rate.
_Avoid_: Frequency (when meaning Log Entry counts)

**Chronic**:
Said of a Pattern that recurs in most weeks of the Data Scope at a steady rate.
_Avoid_: Background noise (as a measured label)

**New Pattern**:
A Pattern first seen recently relative to the end of the Data Scope, above a stated minimum count.
_Avoid_: Novel event

**Cascade**:
Pattern A followed by Pattern B on the same source within a stated window, more often than a baseline. Always reported as a Hypothesis.
_Avoid_: Root cause, causal chain

**Episode**:
An optional merge of Incidents of different Patterns that overlap in time.
_Avoid_: Outage

**Request Failure**:
An OpenStack request or instance lifecycle that ended in ERROR or CRITICAL, an HTTP 5xx status, or did not complete.
_Avoid_: Crash, outage

**Latency Degradation**:
A rise in OpenStack API latency percentiles (p95 or p99) per time bucket against a stated baseline.
_Avoid_: Slowness (in measured statements)

**Phase-Duration Anomaly**:
An OpenStack instance whose spawn or lifecycle phase took unusually long by a robust z-score against the other instances.
_Avoid_: Slow instance (in measured statements)
