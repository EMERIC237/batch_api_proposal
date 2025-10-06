# Why add DynamoDB to the Kendra ingestion pipeline? (Manager Pitch)

## The problem today
- Ingestion is bursty and Kendra throttles when we push too hard.
- Retries cause duplicate work and unnecessary Kendra API calls.
- If a run fails mid-way, we reprocess blindly because the system doesn’t remember what it already indexed.

## What DynamoDB gives us
1) **Idempotency ledger (ProcessedDocs):** We store the version of each document (S3 VersionId or ETag/sha256). If a doc hasn’t changed, we **skip** it. This reduces Kendra calls, shortens run time, and cuts cost.
2) **Job progress & completion (IngestionJobs):** We know exactly how many messages are expected vs processed. We can produce reliable end-of-run summaries and alerts.
3) **Concurrency safety (IngestionLocks):** Prevents accidental double-runs on the same jobId (e.g., duplicate manifest).

## Business benefit
- **Lower Kendra & Lambda cost:** Skip ~X% unchanged docs (historically, most daily deltas are small). Fewer retries thanks to orderly flow and dedupe.
- **Faster SLAs:** We avoid “re-ingest everything” when only 2–5% of docs changed. Runs complete predictably.
- **Operational clarity:** We can answer “what failed and why” with a single `failures.csv` and job counters. Easier on-call and audits.

## Cost vs value
- DynamoDB (on-demand) for three small tables typically costs a few dollars per month at our scale.
- Savings from avoided Kendra calls + shorter Lambda time + fewer failed reruns will exceed that quickly.
- The tables are serverless: no maintenance, automatic scaling, and we can add TTL if desired.

## Implementation plan (low risk, incremental)
- Add three tables (ProcessedDocs, IngestionJobs, IngestionLocks).
- Modify PutWorker to: read doc VersionId/ETag, **skip unchanged**, and **upsert** version on success.
- (Optional) Use IngestionJobs to drive a Summarizer when counts match.
- Roll out behind a flag; measure savings with CloudWatch metrics.
