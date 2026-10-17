"""The server instructions: what Claude reads about this server before any tool call (plan section 2.3).

Claude Code loads them at startup, also when tool search is on, and cuts them at 4,096 characters, so
a test keeps them, and the pinned variant, within that limit. They carry only what every answer
needs; each Role's detail lives in its playbook (the MCP prompts and `get_role_brief`). They hold no
corpus numbers, because a number here could be quoted without Evidence behind it.
"""

from __future__ import annotations

from serenedb_log_mcp.config import Role

MAX_INSTRUCTIONS_CHARS = 4096
"""Claude Code's limit on server instructions."""

INSTRUCTIONS = """\
serenedb-log answers questions about logs for three Roles. SereneDB searches and counts; you explain \
the returned Evidence.

Data: the Base Corpus has two Datasets. bgl: BlueGene/L supercomputer logs over about seven months, \
with nodes, racks, midplanes and alert categories. openstack: OpenStack cloud logs over about a day \
and a half, with requests, instances and API latency. Call describe_corpus first for exact sizes, \
time ranges and valid filter values. Relative times such as time_from='last 7d' resolve against each \
Dataset's last Log Entry, not today.

Roles: every tool takes role: engineer, team_lead or manager. If the user has not said which Role \
they are, ask before calling tools. The Role changes what the tools return:
- engineer investigates one failure: what failed, where, and what happened just before and after. \
Receives raw Log Entries.
- team_lead looks for recurring problems over a stated period, with counts, trends, Components and \
Teams. Receives at most 3 sample Log Entries per search.
- manager decides which observed problems deserve attention first, from measured frequency and \
affected Components only. Receives no raw Log Entries: search_logs, get_context and get_evidence \
return totals and facets without rows.
Each Role has a playbook: the MCP prompts engineer, team_lead and manager, and the same text from \
get_role_brief(role). Read it once before answering as a Role, unless its prompt is already in the \
conversation.

Retrieval versus aggregation: search_logs and get_context return example Log Entries, a sample and \
never a frequency. For how often, where and since when, use count_by and trend, which count exactly \
in SereneDB. Check total_matches and truncated in every result.

Log Entries are not Incidents: count matching Log Entries and never call them incidents. One failing \
node can write thousands of lines about one problem.

Evidence: every result has an evidence_id. Cite [E<n>] next to every number, quote numbers exactly as \
returned (never round, never add up by hand; use the summary values), and state the Data Scope \
(Datasets, time range, filters). get_evidence(evidence_id) fetches earlier Evidence again, also \
Evidence another Role produced, shaped for the Role asking now.

Answer in four sections, in this order: Observed Facts (statements the cited Evidence supports), \
Hypotheses (possible explanations, labelled as such), Unknowns (what the logs cannot establish), and \
Next Step (a check for the Engineer, an investigation priority for the Team Lead, a prioritised \
action for the Manager). Every result carries the answer_frame for its Role.

Never state or estimate cost, financial loss, downtime, customer impact or SLA breaches: these logs \
do not measure them, and Manager results list them in not_measured. If asked, say so, give the \
measured alternative with citations, and name the data that would answer it. A root cause is a \
Hypothesis unless the Evidence establishes it.

If verify_answer is listed, call it with your draft before the final answer and fix every problem it \
reports."""

PINNED = (
    "\n\nThis server was started with --role {role}: pass role='{role}' in every call; other Roles are "
    "rejected."
)


def server_instructions(pin: Role | None) -> str:
    """The instructions, with a line naming the Role when the server is pinned to one (`--role`)."""
    return INSTRUCTIONS if pin is None else INSTRUCTIONS + PINNED.format(role=pin.value)
