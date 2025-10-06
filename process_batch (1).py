import os, json, boto3, time, random
from botocore.config import Config

INDEX_ID = os.environ["KENDRA_INDEX_ID"]
CONTENT_TYPE = os.environ.get("KENDRA_CONTENT_TYPE","PLAIN_TEXT")

s3 = boto3.client("s3")
kendra = boto3.client("kendra", config=Config(retries={"max_attempts": 10}))
MAX_DOCS = 10

def _pair_content_and_metadata(bucket, batch_key):
    prefix = "/".join(batch_key.split("/")[:-1]) + "/"
    files = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]; name = key.split("/")[-1]
            if name.endswith(".json.metadata.json"):
                base = name.replace(".json.metadata.json",""); files.setdefault(base,{})["meta"]=key
            elif name.endswith(".json"):
                base = name.replace(".json",""); files.setdefault(base,{})["content"]=key
    return [g for g in files.values() if "content" in g and "meta" in g]

def handler(event, _ctx):
    bucket = event["bucket"]; batch = event["batch"]; job_id = event["jobId"]
    failed = []; docs = []
    for g in _pair_content_and_metadata(bucket, batch["key"]):
        content = s3.get_object(Bucket=bucket, Key=g["content"])["Body"].read()
        if len(content) > 5*1024*1024:
            failed.append({"DocumentId": g["content"], "ErrorCode":"DocumentSizeExceeded","ErrorMessage":"raw>5MB"}); continue
        meta = json.loads(s3.get_object(Bucket=bucket, Key=g["meta"])["Body"].read().decode())
        attrs = []
        for k,v in meta.items():
            if isinstance(v, list): attrs.append({"Key":k,"Value":{"StringListValue":[str(x) for x in v]}})
            elif isinstance(v,(int,float)): attrs.append({"Key":k,"Value":{"LongValue":int(v)}})
            else: attrs.append({"Key":k,"Value":{"StringValue":str(v)}})
        doc_id = meta.get("pb_sid") or g["content"].split("/")[-1].replace(".json","")
        title  = f"{meta.get('pb_first_name','')} {meta.get('pb_last_name','')} | {meta.get('pb_sid','')}".strip()
        docs.append({"Id":doc_id,"Title":title[:1000],"Attributes":attrs,"Blob":content,"ContentType":CONTENT_TYPE})
    # send in 10s
    for i in range(0, len(docs), MAX_DOCS):
        sub = docs[i:i+MAX_DOCS]; backoff = 0.2
        while True:
            try:
                r = kendra.batch_put_document(IndexId=INDEX_ID, Documents=sub)
                failed.extend(r.get("FailedDocuments", [])); break
            except kendra.exceptions.ThrottlingException:
                time.sleep(backoff + random.random()*0.2); backoff = min(5.0, backoff*2)
    return {"jobId": job_id, "failed": failed}
