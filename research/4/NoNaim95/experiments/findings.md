# #4 experiments findings (subagent report, saved by parent; see r4e_rank_output.md, r4e_evidence_ledger.md, r4e_access_results.json)

## Formula (b) composite
score = 0.25·F + 0.25·S + 0.15·P + 0.15·T + 0.10·R + 0.10·Tm. Each factor is min-max scaled over the ranked set.

| Factor | Definition |
|---|---|
| F | log10(1 + entries per covered day) |
| S | ln(effective sources), where effective sources = 1/Σ(node share)² |
| P | active days / covered days |
| T | log2(last 4 weeks rate / earlier rate), clipped to ±3; neutral below 30 entries |
| R | 1 − days since last seen / span |
| Tm | team count; zero today |

The query takes 17–65 ms over 48 groups.

## Curated set: 41 BGL alert categories + 7 OpenStack ERROR/WARN/CRIT groups
- Top 5 under (b): APPSEV, KERNSTOR, KERNRTSP, OpenStack "Unknown base file", KERNMNTF.
- KERNDTLB's rank under each formula:

| Formula | (a) raw count | (b) composite | (c) Borda | (d) FMEA | (e) Bursts |
|---|---|---|---|---|---|
| KERNDTLB rank | 1 | 13 | 21 | 38 | 46 |

- KERNDTLB has 1.01 effective sources: 99.6% of its entries come from one node.
- Kendall tau: a–b 0.549, b–c 0.659, b–e 0.753, a–d 0.268.
- Weight sensitivity: 1,000 random draws near the default weights give median tau 0.868 and a median top-10 overlap of 9/10. APPSEV is in the top 10 in 100% of draws.
- Dropping the spread factor moves the ranking most (tau 0.60), as intended.
- Borda and FMEA promote groups of only 2 entries. On the full set of 5,108 masked groups every formula gets flooded by register-dump fragments, so the Manager must rank curated groups.

## Team count
Every alert tag and every pattern maps to exactly one Component. So Team count is 1 for every group and carries no signal per group. It only becomes useful across cascades or time windows.

## Same problem for every Role: APPSEV "Link has been severed"
Ledger entries E1–E15. Observed facts:
- At 2006-01-03 03:24:46, KERNMC (L2 dcache parity) fired on compute node R27-M0-NA-C:J11-U11, followed by 134 dump lines and an rts panic at 03:25:05.504.
- 0.26 s after the panic, 128 I/O nodes logged APPSEV until 03:25:26. Nothing else was logged in the 6-minute window.
- All 49,651 APPSEV entries name the same peer, 172.16.96.116.
- APPSEV forms 136 Bursts over 67 days. 47% of Bursts start within 120 s of a KERNMC entry and 25% within 120 s of a KERNRTSP entry. The baseline at 3,618 random hourly probes is 0.33% and 0.39%.
- The last-4-weeks rate is 1.78× the earlier rate.
- APPSEV is spread evenly across 64 racks.

Example answers for all three Roles are in the subagent report. The cost question is declined correctly.

## Access per Role (views + LOGIN roles + grants)
What works:
- Manager: sees aggregate views only. Base tables, `message`, the inverted index, other views, CREATE, SET ROLE, ATTACH and COPY TO are all denied.
- Team Lead: column grants work.

What does not:
- Trust auth means passwords are not checked in our container.
- Any login role can read_csv('/etc/passwd'), glob the data directory, read_parquet over https, and SET enable_external_access=false server-wide.
- pg_views exposes view definitions.

## Gotchas
- integer_division=on.
- JOIN USING drops NULL keys.
- Column grants block DROP ROLE.
- '&' in an @@ string may not mean AND (unverified).
