import json, boto3

def handler(event, _ctx):
    if "Records" in event and event["Records"][0].get("eventSource") == "aws:s3":
        rec = event["Records"][0]
        bucket = rec["s3"]["bucket"]["name"]; key = rec["s3"]["object"]["key"]
        s3 = boto3.client("s3")
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        manifest = json.loads(body)
    else:
        manifest = event
    manifest.setdefault("reportBucket", manifest.get("bucket"))
    return manifest
