import boto3, csv, io
s3 = boto3.client("s3")
def handler(event, _ctx):
    items = event if isinstance(event, list) else event.get("results", [])
    job_id = None; report_bucket = event.get("reportBucket")
    rows = []
    for it in items:
        job_id = job_id or it.get("jobId")
        for f in it.get("failed", []):
            rows.append([job_id, f.get("DocumentId",""), f.get("ErrorCode",""), f.get("ErrorMessage","")])
    key = f"_reports/{job_id}/failures.csv"
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["jobId","documentId","errorCode","errorMessage"])
    for r in rows: w.writerow(r)
    s3.put_object(Bucket=report_bucket, Key=key, Body=buf.getvalue().encode())
    return {"jobId": job_id, "reportKey": key, "failCount": len(rows)}
