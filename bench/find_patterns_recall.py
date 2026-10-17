#!/usr/bin/env python3
"""Does find_patterns help? Recall of the shipped catalogue tools on #7's labelled queries (T26).

Usage (see `make help`):
    uv run --extra vectors python bench/find_patterns_recall.py LABELS.yaml [LABELS.yaml ...]

Each labels file is one of the two hand-labelled query sets of the #7 research, with relevant
Loghub-2.0 EventIds per question; fetch them with

    git show origin/research/7-OrFrederick:research/7/OrFrederick/labels.yaml > labels.yaml
    gh api -H 'Accept: application/vnd.github.raw' > r7e_labels.yaml \
        'repos/NoNaim95/serenedb-log-mcp/contents/research/7/NoNaim95/experiments/r7e_labels.yaml?ref=research/7-NoNaim95'


Both formats are read: OrFrederick's (`bgl:`/`openstack:` lists of `q`, `kind`, `rel`,
`no_relevant`) and NoNaim95's (`BGL:`, `OpenStack:`, `holdout_BGL:`, `holdout_OpenStack:` lists of
`text`, `type`, `relevant`). The EventIds map to our Pattern ids through `pattern.event_id` in
SLM_SCHEMA (Tier 1 Patterns), so SLM_SCHEMA needs Pattern Tier 1 and `make embed-patterns`.

Every question goes through the MCP tools in process, exactly as an agent calls them, with top=50:
search_patterns (BM25, stemming, synonyms), find_patterns mode='semantic' (vectors alone) and
find_patterns mode='hybrid' (the default, RRF of both). It prints R@5, R@10, capped R@10
(hits / min(10, relevant)), P@5 and MRR per set and pooled, R@10 on the paraphrase questions, and
paired bootstrap 95% intervals (5,000 resamples, seed 0) of the pooled differences; questions with
no relevant Pattern (the "disk full" controls) are listed separately with their top similarity.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import psycopg
import yaml
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from psycopg import sql

from serenedb_log_mcp.config import ConfigError, load_settings
from serenedb_log_mcp.server import create_server

METHODS = ("search_patterns", "find_patterns semantic", "find_patterns hybrid")
METRICS = ("R@5", "R@10", "cR@10", "P@5", "MRR")
PARAPHRASE = {"paraphrase", "para"}
TOP = 50
RESAMPLES = 5_000


class BenchError(Exception):
    """A problem the user can fix; printed without a traceback."""


@dataclass(frozen=True)
class Question:
    label_set: str
    dataset: str
    text: str
    kind: str
    relevant: tuple[str, ...]
    """Loghub-2.0 EventIds; empty for a control with no relevant template."""


def read_labels(path: Path) -> list[Question]:
    data = yaml.safe_load(path.read_text())
    found: list[Question] = []
    for key, items in data.items():
        if not isinstance(items, list) or key == "bias":
            continue
        dataset = "bgl" if "bgl" in key.lower() else "openstack"
        name = f"{path.stem}{'/holdout' if key.startswith('holdout') else ''}"
        for item in items:
            text = item.get("q", item.get("text"))
            relevant = () if item.get("no_relevant") else tuple(item.get("rel", item.get("relevant", [])))
            kind = str(item.get("kind", item.get("type")))
            found.append(Question(name, dataset, str(text), kind, relevant))
    return found


def event_ids(dsn: str, schema: str) -> dict[tuple[str, str], str]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            sql.SQL("SELECT dataset, event_id, template_id FROM {} WHERE event_id IS NOT NULL").format(
                sql.Identifier(schema, "pattern")
            )
        ).fetchall()
    return {(str(d), str(e)): str(t) for d, e, t in rows}


def call(server: MCPServer, tool: str, **arguments: Any) -> dict[str, Any]:
    result = anyio.run(lambda: server.call_tool(tool, arguments))
    if not isinstance(result, CallToolResult) or result.structured_content is None:
        raise BenchError(f"{tool} failed: {result!r}")
    return dict(result.structured_content)


def ranked(server: MCPServer, method: str, question: Question) -> tuple[list[str], float | None]:
    """The Pattern ids a method returns, best first, and the best similarity (find_patterns only)."""
    if method == "search_patterns":
        result = call(server, "search_patterns", role="engineer", dataset=question.dataset,
                      query=question.text, top=TOP)  # fmt: skip
    else:
        mode = method.split()[1]
        result = call(server, "find_patterns", role="engineer", dataset=question.dataset,
                      text=question.text, top=TOP, mode=mode)  # fmt: skip
    columns = result["columns"]
    ids = [str(row[columns.index("template_id")]) for row in result["rows"]]
    best = max((row[columns.index("similarity")] for row in result["rows"]), default=None) if (
        "similarity" in columns
    ) else None
    return ids, best


def metrics(ids: Sequence[str], relevant: set[str]) -> list[float]:
    hits5, hits10 = len(set(ids[:5]) & relevant), len(set(ids[:10]) & relevant)
    mrr = next((1 / (i + 1) for i, t in enumerate(ids) if t in relevant), 0.0)
    return [hits5 / len(relevant), hits10 / len(relevant), hits10 / min(10, len(relevant)), hits5 / 5, mrr]


def interval(a: Sequence[float], b: Sequence[float]) -> str:
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    draw = random.Random(0)
    means = sorted(statistics.fmean(draw.choices(diffs, k=len(diffs))) for _ in range(RESAMPLES))
    low, high = means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]
    return f"{statistics.fmean(diffs):+.3f} [{low:+.3f}, {high:+.3f}]"


def table(title: str, scores: dict[str, list[list[float]]], para: dict[str, list[float]]) -> list[str]:
    n = len(scores[METHODS[0]])
    lines = [f"\n{title} (n={n}, paraphrase n={len(para[METHODS[0]])})",
             "| Method | " + " | ".join(METRICS) + " | R@10 paraphrase |",
             "|---|" + "---|" * (len(METRICS) + 1)]  # fmt: skip
    for method in METHODS:
        means = [statistics.fmean(s[i] for s in scores[method]) for i in range(len(METRICS))]
        lines.append(
            f"| {method} | " + " | ".join(f"{m:.3f}" for m in means)
            + f" | {statistics.fmean(para[method]) if para[method] else float('nan'):.3f} |"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="find_patterns_recall.py", description=__doc__.splitlines()[0])
    parser.add_argument("labels", nargs="+", type=Path, help="labels files of the #7 research")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        ids = event_ids(settings.database_url, settings.slm_schema)
        if not ids:
            raise BenchError(f"{settings.slm_schema}.pattern has no Tier 1 Patterns (event_id)")
        server = create_server()
        questions = [q for path in args.labels for q in read_labels(path)]
        per_set: dict[str, dict[str, list[list[float]]]] = {}
        per_set_para: dict[str, dict[str, list[float]]] = {}
        controls: list[str] = []
        for question in questions:
            relevant = {ids[(question.dataset, e)] for e in question.relevant if (question.dataset, e) in ids}
            results = {method: ranked(server, method, question) for method in METHODS}
            if not question.relevant:
                best = results["find_patterns semantic"][1]
                controls.append(
                    f"{question.label_set} {question.dataset} {question.text!r}: search_patterns "
                    f"{len(results['search_patterns'][0])} matches, best similarity {best}"
                )
                continue
            if not relevant:
                continue
            for name in (question.label_set, "pooled"):
                scores = per_set.setdefault(name, {m: [] for m in METHODS})
                para = per_set_para.setdefault(name, {m: [] for m in METHODS})
                for method in METHODS:
                    found = metrics(results[method][0], relevant)
                    scores[method].append(found)
                    if question.kind in PARAPHRASE:
                        para[method].append(found[1])
        for name, scores in per_set.items():
            for line in table(name, scores, per_set_para[name]):
                print(line)
        pooled = per_set["pooled"]
        for metric, index in (("R@10", 1), ("MRR", 4)):
            for a, b in ((2, 0), (1, 0), (2, 1)):
                left = [s[index] for s in pooled[METHODS[a]]]
                right = [s[index] for s in pooled[METHODS[b]]]
                print(f"pooled {metric} {METHODS[a]} - {METHODS[b]}: {interval(left, right)}")
        print("\nControls with no relevant Pattern:")
        for line in controls:
            print(f"  {line}")
    except (BenchError, ConfigError, psycopg.Error, OSError) as exc:
        print(f"find_patterns_recall: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
