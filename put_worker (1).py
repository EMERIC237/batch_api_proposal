import os, json, boto3, time, random, io, csv, uuid
from botocore.config import Config
kendra = boto3.client("kendra", config=Config(retries={"max_attempts": 10}))
s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb") if os.environ.get("DOCS_TABLE") else None
INDEX_ID = os.environ["KENDRA_INDEX_ID"]; REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
def _write(job_id, items):
    if not items: return
    key = f"_reports/{job_id}/put/part-{uuid.uuid4()}.csv"; buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["jobId","documentId","errorCode","errorMessage"])
    for f in items: w.writerow([job_id, f.get("DocumentId",""), f.get("ErrorCode",""), f.get("ErrorMessage","")])
    s3.put_object(Bucket=REPORTS_BUCKET, Key=key, Body=buf.getvalue().encode())
def handler(event, _ctx):
    for rec in event["Records"]:
        body = json.loads(rec["body"]); job_id = body["jobId"]; fails = []; payload = []
        s3c = boto3.client("s3")
        table = ddb.Table(DOCS_TABLE) if DOCS_TABLE else None
        for d in body["docs"]:
            obj = s3c.get_object(Bucket=d["s3Bucket"], Key=d["s3Key"])["Body"].read()
            if len(obj) > 5*1024*1024: fails.append({"DocumentId":d["documentId"],"ErrorCode":"DocumentSizeExceeded","ErrorMessage":"raw>5MB"}); continue
            meta = json.loads(s3c.get_object(Bucket=d["s3Bucket"], Key=d["metaKey"])["Body"].read().decode())
            attrs = []
            for k,v in meta.items():
                if isinstance(v, list): attrs.append({"Key":k,"Value":{"StringListValue":[str(x) for x in v]}})
                elif isinstance(v,(int,float)): attrs.append({"Key":k,"Value":{"LongValue":int(v)}})
                else: attrs.append({"Key":k,"Value":{"StringValue":str(v)}})
            payload.append({"Id":d["documentId"],"Attributes":attrs,"Blob":obj,"ContentType":"PLAIN_TEXT"})
        backoff = 0.2
        while True and payload:
            try:
                r = kendra.batch_put_document(IndexId=INDEX_ID, Documents=payload); fails += r.get("FailedDocuments", []); break
            except kendra.exceptions.ThrottlingException:
                time.sleep(backoff + random.random()*0.2); backoff = min(5.0, backoff*2)
        _write(job_id, fails)
        # mark successes in DDB
        if DOCS_TABLE and payload:
            table = ddb.Table(DOCS_TABLE)
            for p in payload:
                try:
                    table.put_item(Item={
                        "documentId": p["Id"],
                        "sourcePrefix": body["docs"][0]["s3Bucket"]+":"+body["docs"][0]["s3Key"],
                        "version": version if 'version' in locals() else "",
                        "lastStatus": "SUCCESS",
                        "lastUpdatedAt": __import__('datetime').datetime.utcnow().isoformat()+"Z"
                    })
                except Exception:
                    pass
    return {"batchItemFailures":[]}
