import os, json, boto3, time, random, io, csv, uuid
from botocore.config import Config
kendra = boto3.client("kendra", config=Config(retries={"max_attempts": 10}))
s3 = boto3.client("s3")
INDEX_ID = os.environ["KENDRA_INDEX_ID"]; REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
def _write(job_id, items):
    if not items: return
    key = f"_reports/{job_id}/delete/part-{uuid.uuid4()}.csv"; buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["jobId","documentId","errorCode","errorMessage"])
    for f in items: w.writerow([job_id, f.get("DocumentId",""), f.get("ErrorCode",""), f.get("ErrorMessage","")])
    s3.put_object(Bucket=REPORTS_BUCKET, Key=key, Body=buf.getvalue().encode())
def handler(event, _ctx):
    for rec in event["Records"]:
        body = json.loads(rec["body"]); job_id = body["jobId"]; ids = body["documentIds"]; fails = []; i = 0
        while i < len(ids):
            chunk = ids[i:i+10]; i += len(chunk); backoff = 0.2
            while True:
                try:
                    r = kendra.batch_delete_document(IndexId=INDEX_ID, DocumentIdList=chunk); fails += r.get("FailedDocuments", []); break
                except kendra.exceptions.ThrottlingException:
                    time.sleep(backoff + random.random()*0.2); backoff = min(5.0, backoff*2)
        _write(job_id, fails)
    return {"batchItemFailures":[]}
