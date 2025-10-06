# Kendra Ingestion Pipeline — Step Functions **and** SQS FIFO Options

This repo provides two production-grade ways to ingest JSON (+ metadata) documents from S3 into **Amazon Kendra** using the **Documents API** (`BatchPutDocument` / `BatchDeleteDocument`). It adapts your current Lambda and removes recursion and data-source sync waits.

You can deploy **either**:
- **Option A — Step Functions (SFN) Orchestration:** a single state machine coordinates batch processing, retries, failure aggregation, and notifications.
- **Option B — SQS FIFO Worker Pool:** a producer enqueues packed jobs, and a small pool of worker Lambdas steadily pushes to Kendra with bounded concurrency.

> **Why not Kendra S3 connector?** You tested it and it was too slow. These options use the Documents API directly and explicitly honor its quotas (≤10 docs per request, ≤50 MB total, ≤5 MB per doc).

---

## Should we delete `manifest.json` after processing?
**Recommendation:** **Do not delete immediately.** Keep manifests for **auditability**, reproducibility, and troubleshooting. Instead:
- Move them to an **`_archive/`** prefix when the job finishes.
- Apply an **S3 Lifecycle rule** to expire archived manifests after **30–90 days**.
- This gives you a reliable paper trail without long-term storage cost.

---

## Repository Layout

```
kendra-ingestion/
├─ README.md
├─ sample/
│  └─ manifest.json            # example job trigger payload
├─ state_machines/
│  └─ orchestrator.asl.json    # Step Functions state machine (ASL JSON)
├─ lambdas/
│  ├─ load_manifest.py         # SFN: loads/validates manifest (or from S3 event)
│  ├─ list_batches.py          # SFN: lists batch objects under prefix
│  ├─ process_batch.py         # SFN & SQS: reads pairs, packs & calls BatchPutDocument
│  ├─ aggregate_failures.py    # SFN: merges failures to a single CSV in S3
│  ├─ acquire_lock.py          # (optional) DDB lock for jobId
│  ├─ release_lock.py          # (optional) DDB unlock
│  ├─ producer.py              # SQS: builds & enqueues PUT/DELETE messages
│  ├─ put_worker.py            # SQS: worker for BatchPutDocument
│  ├─ del_worker.py            # SQS: worker for BatchDeleteDocument
│  └─ summarizer.py            # SQS: merges per-part CSVs → failures.csv & notifies
├─ terraform/
│  ├─ main.tf                  # skeleton infra for both options
│  ├─ variables.tf
│  └─ outputs.tf
└─ diagrams/
   ├─ sfn.drawio               # architecture diagram (Step Functions option)
   └─ sqs.drawio               # architecture diagram (SQS FIFO option)
```

---

## Prerequisites

- AWS account & credentials
- Terraform ≥ 1.5
- S3 buckets created or created by Terraform:
  - **Ingestion bucket** (batches + manifest)
  - **Reports bucket** (`_reports/<jobId>/…` and archived manifests)
- Kendra index ID available
- (Optional) DynamoDB if you want robust **idempotency** and **locking**

> **Can we avoid DynamoDB?** Yes, to start. You can rely on unique `jobId` and single trigger per job. Later add DDB tables for idempotency (skip unchanged docs), job counters, and lock safety.

---

## Option A — Step Functions (SFN) Orchestrated Pipeline

### Flow
1. Ingester writes N batch files under `s3://<ingestion-bucket>/<prefix>/batches/…` and finally writes `…/_control/manifest.json`.
2. S3 **ObjectCreated** (manifest) triggers Start Execution (via EventBridge or a small “starter” Lambda).
3. State machine:
   - **LoadManifest** → **ListBatches** → **Map(ProcessBatch)** (max 1–4 concurrency) → **AggregateFailures** → **Notify**.
4. Output: `s3://<reports-bucket>/_reports/<jobId>/failures.csv` and SNS notification.

### Deploy (sketch)
```bash
cd terraform
terraform init
terraform apply   -var="aws_region=us-east-1"   -var="ingestion_bucket=YOUR-INGESTION-BUCKET"   -var="reports_bucket=YOUR-REPORTS-BUCKET"   -var="kendra_index_id=YOUR-KENDRA-INDEX-ID"   -var="deploy_option=sfn"
```
> Zip each Lambda in `lambdas/` (or let Terraform archive_file do this). Point to the zip paths in `aws_lambda_function` resources.

### Run
- Upload batches and `manifest.json` under your prefix. The state machine runs automatically and writes the failure report.

---

## Option B — SQS FIFO Worker Pool

### Flow
1. Ingester writes batches and `manifest.json`.
2. **Producer Lambda** (triggered by manifest) pairs `*.json` + `*.json.metadata.json`, **packs** ≤10 docs ≤50 MB, and enqueues messages to:
   - `kendra-put.fifo` (PUT jobs)
   - `kendra-del.fifo` (DELETE jobs)
3. **PutWorker** and **DelWorker** Lambdas consume with **bounded concurrency** and backoff on throttling.
4. Each worker writes partial failure CSVs to `s3://…/_reports/<jobId>/put|delete/part-*.csv`.
5. A **Summarizer** merges parts to `failures.csv` and sends an SNS notification.

### Deploy (sketch)
```bash
cd terraform
terraform apply   -var="aws_region=us-east-1"   -var="ingestion_bucket=YOUR-INGESTION-BUCKET"   -var="reports_bucket=YOUR-REPORTS-BUCKET"   -var="kendra_index_id=YOUR-KENDRA-INDEX-ID"   -var="deploy_option=sqs"
```

### Message shapes (on the queues)

```jsonc
// kendra-put.fifo message body
{{
  "action": "put",
  "jobId": "phonebook-2025-10-05T12-00Z",
  "docs": [
    {{
      "documentId": "emp-123",
      "s3Bucket": "ingestion-bucket",
      "s3Key": "KendraDocuments/phonebook/2025-10-05/batches/emp-123.json",
      "metaKey": "KendraDocuments/phonebook/2025-10-05/batches/emp-123.json.metadata.json"
    }}
  ]
}}

// kendra-del.fifo message body
{{
  "action": "delete",
  "jobId": "phonebook-2025-10-05T12-00Z",
  "documentIds": ["emp-888","emp-999"]
}}
```

Each SQS send uses:
- `MessageGroupId = "put-<shard>"` or `"del-<shard>"` (small # of shards → bounded parallelism).
- `MessageDeduplicationId = SHA-256 of (jobId + payloadDigest)` to suppress duplicates.

---

## Failure Reporting

- **SFN option:** the Map state aggregates all `FailedDocuments` into `failures.csv`.
- **SQS option:** workers write `put/delete/part-*.csv`, then `summarizer.py` merges into a final `failures.csv` and publishes SNS.

CSV header: `jobId,documentId,errorCode,errorMessage`

---

## Security & Cost

- SSE-S3 or SSE-KMS on all buckets; least-privilege IAM.
- Graviton (arm64) Lambdas to cut cost.
- Keep worker concurrency low (2–4 puts, 4–8 deletes). SQS costs are minimal (~$0.40 / 1M req).

---

## Sample manifest

See `sample/manifest.json`. Use one per job and **archive** it afterward (don’t delete immediately).
