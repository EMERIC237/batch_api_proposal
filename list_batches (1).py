import boto3

def handler(event, _ctx):
    s3 = boto3.client("s3")
    bucket = event["bucket"]; prefix = event.get("prefix")
    batches = event.get("batches", [])
    if prefix and not batches:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json") and "/_control/" not in key:
                    batches.append({"key": key})
    event["batches"] = batches
    return event
