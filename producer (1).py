import os, json, boto3, hashlib
s3  = boto3.client("s3"); sqs = boto3.client("sqs")
PUT_Q_URL = os.environ["PUT_QUEUE_URL"]; DEL_Q_URL = os.environ.get("DEL_QUEUE_URL","")
PUT_SHARDS = int(os.environ.get("PUT_SHARDS","3")); DEL_SHARDS = int(os.environ.get("DEL_SHARDS","6"))
def _hash(s): import hashlib; return int(hashlib.md5(s.encode()).hexdigest(), 16)
def _pair(bucket, prefix):
    files = {}; p = s3.get_paginator("list_objects_v2")
    for page in p.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]; name = key.split("/")[-1]
            if name.endswith(".json.metadata.json"):
                base = name.replace(".json.metadata.json",""); files.setdefault(base,{})["meta"]=key
            elif name.endswith(".json"):
                base = name.replace(".json",""); files.setdefault(base,{})["content"]=key
    return [g for g in files.values() if "content" in g and "meta" in g]
def handler(event, _ctx):
    if "Records" in event and event["Records"][0].get("eventSource") == "aws:s3":
        rec = event["Records"][0]; bucket = rec["s3"]["bucket"]["name"]; key = rec["s3"]["object"]["key"]
        m = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode())
    else: m = event
    job_id = m["jobId"]; bucket = m["bucket"]; prefix = m["prefix"]; pairs = _pair(bucket, prefix)
    batch, cur = [], []
    for g in pairs:
        if len(cur) == 10: batch.append(cur); cur = []
        content_key = g["content"]; doc_id = content_key.split("/")[-1].replace(".json","")
        cur.append({"documentId":doc_id,"s3Bucket":bucket,"s3Key":content_key,"metaKey":g["meta"]})
    if cur: batch.append(cur)
    for i,docs in enumerate(batch):
        shard = _hash(job_id+str(i)) % PUT_SHARDS
        body = {"action":"put","jobId":job_id,"docs":docs}
        sqs.send_message(QueueUrl=PUT_Q_URL, MessageBody=json.dumps(body),
            MessageGroupId=f"put-{shard}", MessageDeduplicationId=hashlib.sha256(json.dumps(body).encode()).hexdigest())
    for i in range(0, len(m.get("deletes",[])), 10):
        chunk = m["deletes"][i:i+10]; shard = _hash(job_id+'del'+str(i)) % DEL_SHARDS
        body = {"action":"delete","jobId":job_id,"documentIds":chunk}
        if DEL_Q_URL: sqs.send_message(QueueUrl=DEL_Q_URL, MessageBody=json.dumps(body),
            MessageGroupId=f"del-{shard}", MessageDeduplicationId=hashlib.sha256(json.dumps(body).encode()).hexdigest())
    return {"queuedPutBatches": len(batch), "queuedDeletes": len(m.get("deletes",[]))}
