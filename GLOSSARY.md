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
