import os, boto3, csv, io, json
s3 = boto3.client("s3"); SNS = boto3.client("sns")
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]; SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN","")
def handler(event, _ctx):
    job_id = event["jobId"]; base = f"_reports/{job_id}/"
    parts = []; resp = s3.list_objects_v2(Bucket=REPORTS_BUCKET, Prefix=base)
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if "/part-" in key: parts.append(key)
    rows = []
    for key in parts:
        body = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)["Body"].read().decode().splitlines()
        rdr = csv.reader(body); next(rdr, None)
        for r in rdr: rows.append(r)
    outkey = f"{base}failures.csv"; buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["jobId","documentId","errorCode","errorMessage"])
    for r in rows: w.writerow(r)
    s3.put_object(Bucket=REPORTS_BUCKET, Key=outkey, Body=buf.getvalue().encode())
    msg = {"jobId":job_id,"failures":len(rows),"reportKey":outkey}
    if SNS_TOPIC_ARN: SNS.publish(TopicArn=SNS_TOPIC_ARN, Message=json.dumps(msg))
    return msg
