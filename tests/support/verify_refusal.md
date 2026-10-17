## Observed Facts
**Short answer: these logs cannot tell you what the KERNDTLB storm cost.** They record failures, not money, so any euro figure I gave you would be invented.

What the logs do measure for KERNDTLB:
- 152,734 Log Entries [E1], 43.83% of all alert Log Entries in BGL [E1].
- They form 3 Incidents [E8]; one of them, on a single node, holds 152,182 of those Log Entries [E3].
- By comparison, KERNMNTF has 83 Incidents across 64 midplanes in 9 active weeks [E8].

## Hypotheses
- Because one node wrote almost all of the volume, the business exposure is probably narrower than the line count suggests; that is an inference, not a measurement.
- The storm may have tied up the node until someone intervened, but these logs do not show how long it was unavailable.

## Unknowns
- Cost or financial loss: these logs hold no prices, billing or staff time.
- Downtime: there are no availability or uptime records in the corpus.
- Customer impact: no customer, user or SLA data is in these logs.

## Next Step
1. If you need a cost figure, ask finance or operations for the node-hours lost and the staff time spent on 2005-06-11; I can supply the exact time window from [E3].
2. Meanwhile, prioritise KERNMNTF over KERNDTLB: it recurs across many midplanes [E8].
