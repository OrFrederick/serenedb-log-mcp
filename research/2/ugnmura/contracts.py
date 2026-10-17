"""Proposed research contracts, not product handlers or a confirmed decision.

Generate JSON Schemas with `python contracts.py`. All SQL values must be bound.
Human decision required on aggregate truncation semantics; see report.md.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator,create_model

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Scope(Contract):
    datasets: Annotated[list[Literal['BGL','OpenStack']],Field(min_length=1,max_length=2)]
    # Research snapshot's printed timestamps are naive. Official loader semantics
    # must replace this declared basis before product use.
    time_basis: Literal['dataset_printed_naive']='dataset_printed_naive'
    start: datetime|None=None
    end: datetime|None=None
    full_time_range: bool=False
    components: Annotated[list[str],Field(max_length=20)]=[]
    levels: Annotated[list[str],Field(max_length=10)]=[]
    labels: Annotated[list[str],Field(max_length=50)]=[]
    source_files: Annotated[list[str],Field(max_length=5)]=[]
    @model_validator(mode='after')
    def valid_scope(self):
        if len(set(self.datasets))!=len(self.datasets):raise ValueError('Datasets must be distinct')
        bounded=self.start is not None and self.end is not None
        if self.full_time_range==bounded:raise ValueError('Choose full_time_range or both start/end')
        if not bounded and (self.start is not None or self.end is not None):raise ValueError('Both boundaries required')
        if bounded:
            if self.start.tzinfo is not None or self.end.tzinfo is not None:raise ValueError('Printed-time research scope requires naive timestamps')
            if self.start>=self.end:raise ValueError('Use start < end; half-open interval')
        return self

class Dimension(str,Enum):
    dataset='dataset';component='component';team='team';level='level';label='label'
    node='node';rack='rack';midplane='midplane';template_id='template_id';source_file='source_file'

class Search(Contract):
    investigation_id: str
    scope: Scope
    query: Annotated[str,Field(min_length=1,max_length=256)]
    mode: Literal['all_terms','any_terms','phrase','fuzzy']='all_terms'
    edit_distance: Literal[0,1]=1
    limit: Annotated[int,Field(ge=1,le=100)]=20
    cursor: str|None=None
    @model_validator(mode='after')
    def fuzzy_is_token(self):
        if self.mode=='fuzzy' and (len(self.query)>64 or any(c.isspace() for c in self.query)):
            raise ValueError('Fuzzy mode requires one token <=64 characters')
        return self

class EntryKey(Contract):
    dataset: Literal['BGL','OpenStack']
    source_file: str
    entry_id: Annotated[int,Field(ge=1)]

class Context(Contract):
    investigation_id: str
    anchor: EntryKey
    correlation: Literal['node','request_id','instance_id']
    before_seconds: Annotated[int,Field(ge=0,le=3600)]=60
    after_seconds: Annotated[int,Field(ge=0,le=3600)]=60
    limit: Annotated[int,Field(ge=1,le=100)]=30
    cursor: str|None=None

class CountBy(Contract):
    investigation_id: str
    scope: Scope
    dimensions: Annotated[list[Dimension],Field(min_length=1,max_length=3)]
    limit: Annotated[int,Field(ge=1,le=100)]=20
    cursor: str|None=None

class Trend(Contract):
    investigation_id: str
    scope: Scope
    bucket: Literal['1 minute','5 minutes','1 hour','1 day','1 week']='1 day'
    group_by: Dimension|None=None
    max_cells: Annotated[int,Field(ge=1,le=500)]=200
    cursor: str|None=None

class Patterns(Contract):
    investigation_id: str
    scope: Scope
    parser_version: str
    limit: Annotated[int,Field(ge=1,le=100)]=20
    cursor: str|None=None

class Latency(Contract):
    investigation_id: str
    scope: Scope
    metric: Literal['http_request_seconds','instance_build_seconds']
    group_by: Literal['dataset','component','endpoint','source_file','instance_id']='source_file'
    threshold_seconds: Annotated[float,Field(ge=0)]|None=None
    limit: Annotated[int,Field(ge=1,le=100)]=20
    cursor: str|None=None

class Comparison(Contract):
    investigation_id: str
    before: Scope
    after: Scope
    metric: Literal['matching_log_entries','http_request_seconds','instance_build_seconds']
    group_by: Dimension|None=None
    @model_validator(mode='after')
    def comparable(self):
        a=self.before;b=self.after
        fields=['datasets','time_basis','components','levels','labels','source_files']
        if any(getattr(a,k)!=getattr(b,k) for k in fields):raise ValueError('Keep Data Scope filters constant; compare time bounds only')
        if a.full_time_range or b.full_time_range:raise ValueError('Explicit comparison time bounds required')
        return self

class GroupEntries(Contract):
    investigation_id: str
    scope: Scope
    key: Literal['label_node','label','template_node','instance_id']
    parser_version: str|None=None
    gap_seconds: Annotated[int,Field(ge=1,le=3600)]=5
    max_span_seconds: Annotated[int,Field(ge=1,le=86400)]|None=None
    limit: Annotated[int,Field(ge=1,le=100)]=20
    cursor: str|None=None

class EvidenceLookup(Contract):
    investigation_id: str
    evidence_id: str
    page: Annotated[int,Field(ge=0)]=0
    # Fetching original Evidence never silently reruns current data.

class Describe(Contract):
    investigation_id: str
    datasets: list[Literal['BGL','OpenStack']]=['BGL','OpenStack']

class Query(Contract):
    purpose: str
    sql: str
    parameters: list[object]
    query_ms: Annotated[float,Field(ge=0)]
    snapshot_id: str

class Envelope(Contract):
    investigation_id: str
    evidence_id: str
    schema_version: Literal['research-proposal-1']='research-proposal-1'
    data_scope: dict[str,object]
    queries: list[Query]
    query_ms: Annotated[float,Field(ge=0)]
    total_match_count: Annotated[int,Field(ge=0)]
    result_row_unit: Literal['log_entries','groups','buckets','comparisons','metadata']
    total_result_rows: Annotated[int,Field(ge=0)]
    returned_count: Annotated[int,Field(ge=0)]
    # Kept literal to current glossary, even for aggregation. Human clarification
    # requested: compare returned rows with total *result* rows instead.
    truncated: bool
    result_truncated: bool
    content_truncated: bool=False
    next_cursor: str|None=None
    count_is_exact: bool
    rows: list[dict[str,object]]
    warnings: list[str]=[]
    @model_validator(mode='after')
    def counts_consistent(self):
        if self.returned_count!=len(self.rows):raise ValueError('returned_count must equal row array length')
        if self.returned_count>self.total_result_rows:raise ValueError('Returned rows exceed result rows')
        if self.result_truncated!=(self.returned_count<self.total_result_rows):raise ValueError('Incorrect result_truncated')
        if self.truncated!=(self.returned_count<self.total_match_count):raise ValueError('Current literal glossary truncation flag mismatch')
        return self

class LogEntryRow(Contract):
    key: EntryKey
    timestamp: datetime|None
    component: str|None
    level: str|None
    node: str|None
    message: str|None
    snippet: str|None=None
    score: float|None=None
    correlation_key: str|None=None
    content_truncated: bool=False

class CorpusRow(Contract):
    dataset: str
    log_entry_count: int
    timestamped_entries: int
    first_printed_time: datetime|None
    last_printed_time: datetime|None
    components: int
    corpus_version: str
    loader_version: str
    time_basis: str
    capabilities: list[str]
    limitations: list[str]

class CountRow(Contract):
    dimensions: dict[str,str|None]
    log_entry_count: int
    distinct_nodes: int|None=None
    distinct_components: int|None=None
    unmapped_log_entries: int|None=None

class TrendRow(Contract):
    dataset: str
    bucket_start: datetime
    bucket_end: datetime
    dimensions: dict[str,str|None]
    log_entry_count: int
    coverage: Literal['complete','partial','unknown']

class PatternRow(Contract):
    dataset: str
    component: str|None
    template_id: str|None
    parser_version: str
    assignment_status: Literal['catalogue','catalogue_specificity','ambiguous','unmatched','empty_content','header_unparsed']
    template_text: str|None
    log_entry_count: int
    other_pattern_entries: int

class LatencyRow(Contract):
    dimensions: dict[str,str|None]
    metric: Literal['http_request_seconds','instance_build_seconds']
    timed_log_entries: int
    untimed_log_entries: int
    p50_s: float|None
    p95_s: float|None
    p99_s: float|None
    maximum_s: float|None
    above_threshold_count: int|None
    quantile_method: Literal['quantile_cont']='quantile_cont'

class ComparisonRow(Contract):
    dimensions: dict[str,str|None]
    metric: str
    before_value: float|int|None
    after_value: float|int|None
    before_exposure_s: float
    after_exposure_s: float
    absolute_change: float|int|None
    percent_change: float|None
    undefined_reason: str|None

class TemporalGroupRow(Contract):
    policy_version: str
    candidate_group_id: str
    key: dict[str,str|None]
    first_epoch_s: int
    last_epoch_s: int
    log_entry_count: int
    distinct_nodes: int|None
    distinct_racks: int|None
    distinct_components: int
    boundary_censored: bool
    interpretation: Literal['candidate_temporal_group']='candidate_temporal_group'

class SavedEvidenceRow(Contract):
    original_evidence_id: str
    original_investigation_id: str
    content_sha256: str
    artifact_uri: str
    original_queries: list[Query]
    original_result_page: dict[str,object]
    operation: Literal['retrieve_saved_evidence']='retrieve_saved_evidence'

class Failure(Contract):
    investigation_id: str
    evidence_id: str
    status: Literal['error']='error'
    code: Literal['invalid_filter','unsupported_capability','timeout','stale_cursor','permission_denied','database_unavailable']
    message: str
    retryable: bool
    recovery: str
    # No fabricated zero counts on failed calculations.
    total_match_count: None=None

ROWS={'describe_corpus':CorpusRow,'search_logs':LogEntryRow,'get_context':LogEntryRow,
 'count_by':CountRow,'trend':TrendRow,'top_patterns':PatternRow,'latency_stats':LatencyRow,
 'compare_periods':ComparisonRow,'group_entries':TemporalGroupRow,'get_evidence':SavedEvidenceRow}

TOOLS={
 'describe_corpus':(Describe,'Discover exact Dataset coverage, time basis, schema and supported capabilities; use before selecting filters. Returns Dataset metadata and quality/ownership coverage.'),
 'search_logs':(Search,'Retrieve bounded full-text Log Entries with declared all/any/phrase/fuzzy semantics. Use for discovery and examples; use count_by/trend for frequency. Returns stable keys, snippets, exact total and continuation.'),
 'get_context':(Context,'Read bounded before/after Log Entries for an existing anchor on the same node/request/instance. Reject absent correlation ids; never broaden them to the whole Dataset.'),
 'count_by':(CountBy,'Calculate exact matching Log Entry counts by allowlisted dimensions over the full declared Data Scope. Groups, total groups and source-entry totals are distinct.'),
 'trend':(Trend,'Calculate counts in fixed time buckets over the declared Data Scope. Disclose incomplete coverage and return every bucket or an explicit continuation; samples are not trends.'),
 'top_patterns':(Patterns,'Rank versioned message templates by matching Log Entry count, with Component identity and unmatched/ambiguous/empty diagnostics. Never treat template counts as Incident counts.'),
 'latency_stats':(Latency,'Calculate timed-operation count and p50/p95/p99/max seconds for HTTP requests OR instance builds. Never mix these durations. Include threshold counts and missing-duration coverage.'),
 'compare_periods':(Comparison,'Compare the same metrics/filters in two explicit time intervals. Return both counts, elapsed denominators and rates; zero baseline percentage change is null with a reason.'),
 'group_entries':(GroupEntries,'Derive policy-defined temporal groups with threshold sensitivity and spread. These are candidate groups, not verified Incidents; retain policy and boundary-censor flags.'),
 'get_evidence':(EvidenceLookup,'Retrieve immutable original Evidence and its SQL/parameters/results for this investigation. Does not silently rerun SQL; a deliberate replay creates new Evidence linked to the original.')}

if __name__=='__main__':
    out={'status':'draft_requires_glossary_clarification','tools':[],'shared_output_schema':Envelope.model_json_schema()}
    for name,(model,description) in TOOLS.items():
        result_model=create_model(name.title().replace('_','')+'Result',__base__=Envelope,rows=(list[ROWS[name]],...))
        from pydantic import TypeAdapter
        out['tools'].append({'name':name,'description':description,'inputSchema':model.model_json_schema(),
          'outputSchema':TypeAdapter(result_model|Failure).json_schema(),
          'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False,'openWorldHint':False}})
    Path(__file__).with_name('tool-contracts.json').write_text(json.dumps(out,indent=2))
    for name,model,data in [('search',Search,{'investigation_id':'run1','scope':{'datasets':['BGL'],'full_time_range':True},'query':'machine check','mode':'phrase'}),
      ('group',GroupEntries,{'investigation_id':'run1','scope':{'datasets':['BGL'],'full_time_range':True},'key':'label_node'})]:model.model_validate(data)
    print('Generated ten draft input/output schemas; two valid examples parsed')
