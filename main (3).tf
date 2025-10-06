terraform {
  required_version = ">= 1.5.0"
  required_providers { aws = { source = "hashicorp/aws", version = ">= 5.0" } }
}
provider "aws" { region = var.aws_region }

locals {
  lambda_arch = "arm64"
  runtime     = "python3.11"
}

data "aws_caller_identity" "current" {}

# ---------------- IAM ROLES ----------------
resource "aws_iam_role" "lambda_exec" {
  name = "kendra-lambda-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = "sts:AssumeRole", Effect = "Allow",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy_attachment" "lambda_basic_attach" {
  name       = "lambda-basic-exec-attach"
  roles      = [aws_iam_role.lambda_exec.name]
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Allow Lambdas to read S3, call Kendra, use SQS, and put to DDB (optional)
resource "aws_iam_policy" "lambda_kendra_policy" {
  name   = "kendra-lambda-access"
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect="Allow", Action=["kendra:BatchPutDocument","kendra:BatchDeleteDocument"], Resource="*" },
      { Effect="Allow", Action=["s3:GetObject","s3:ListBucket","s3:PutObject"], Resource=["arn:aws:s3:::${var.ingestion_bucket}", "arn:aws:s3:::${var.ingestion_bucket}/*", "arn:aws:s3:::${var.reports_bucket}", "arn:aws:s3:::${var.reports_bucket}/*"] },
      { Effect="Allow", Action=["sqs:SendMessage","sqs:SendMessageBatch","sqs:ReceiveMessage","sqs:DeleteMessage","sqs:GetQueueAttributes","sqs:ChangeMessageVisibility"], Resource="*" },
      { Effect="Allow", Action=["dynamodb:PutItem","dynamodb:GetItem","dynamodb:UpdateItem","dynamodb:DeleteItem","dynamodb:Query"], Resource="*" }
    ]
  })
}

resource "aws_iam_policy_attachment" "lambda_kendra_attach" {
  name       = "lambda-kendra-attach"
  roles      = [aws_iam_role.lambda_exec.name]
  policy_arn = aws_iam_policy.lambda_kendra_policy.arn
}

# --------------- LAMBDAS (paths reference zipped code) ----------------
# Note: zip lambdas under ../lambdas_zips/*.zip or switch to archive_file.
resource "aws_lambda_function" "process_batch" {
  function_name = "kendra-process-batch"
  role          = aws_iam_role.lambda_exec.arn
  architectures = [local.lambda_arch]
  handler       = "process_batch.handler"
  runtime       = local.runtime
  filename      = "${path.module}/../lambdas_zips/process_batch.zip"
  environment {
    variables = {
      KENDRA_INDEX_ID     = var.kendra_index_id
      KENDRA_CONTENT_TYPE = "PLAIN_TEXT"
    }
  }
}

resource "aws_lambda_function" "producer" {
  function_name = "kendra-producer"
  role          = aws_iam_role.lambda_exec.arn
  architectures = [local.lambda_arch]
  handler       = "producer.handler"
  runtime       = local.runtime
  filename      = "${path.module}/../lambdas_zips/producer.zip"
  environment {
    variables = {
      PUT_QUEUE_URL = aws_sqs_queue.put.id
      DEL_QUEUE_URL = aws_sqs_queue.del.id
      PUT_SHARDS    = "3"
      DEL_SHARDS    = "6"
    }
  }
}

resource "aws_lambda_function" "put_worker" {
  function_name = "kendra-put-worker"
  role          = aws_iam_role.lambda_exec.arn
  architectures = [local.lambda_arch]
  handler       = "put_worker.handler"
  runtime       = local.runtime
  reserved_concurrent_executions = 3
  filename      = "${path.module}/../lambdas_zips/put_worker.zip"
  environment {
    variables = {
      KENDRA_INDEX_ID = var.kendra_index_id
      REPORTS_BUCKET  = var.reports_bucket
      DOCS_TABLE      = aws_dynamodb_table.processed_docs.name
    }
  }
}

resource "aws_lambda_function" "del_worker" {
  function_name = "kendra-del-worker"
  role          = aws_iam_role.lambda_exec.arn
  architectures = [local.lambda_arch]
  handler       = "del_worker.handler"
  runtime       = local.runtime
  reserved_concurrent_executions = 6
  filename      = "${path.module}/../lambdas_zips/del_worker.zip"
  environment {
    variables = {
      KENDRA_INDEX_ID = var.kendra_index_id
      REPORTS_BUCKET  = var.reports_bucket
    }
  }
}

# --------------- SQS QUEUES + DLQs ----------------
resource "aws_sqs_queue" "put_dlq" {
  name       = "kendra-put-dlq.fifo"
  fifo_queue = true
}

resource "aws_sqs_queue" "del_dlq" {
  name       = "kendra-del-dlq.fifo"
  fifo_queue = true
}

resource "aws_sqs_queue" "put" {
  name                        = "kendra-put.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.put_dlq.arn
    maxReceiveCount     = 6
  })
}

resource "aws_sqs_queue" "del" {
  name                        = "kendra-del.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.del_dlq.arn
    maxReceiveCount     = 6
  })
}

# -------------- EVENT SOURCE MAPPINGS (SQS → Lambda) --------------
resource "aws_lambda_event_source_mapping" "put_es" {
  event_source_arn            = aws_sqs_queue.put.arn
  function_name               = aws_lambda_function.put_worker.arn
  batch_size                  = 2
  maximum_batching_window_in_seconds = 2
  function_response_types     = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 1 # combined with reserved concurrency controls throughput
  }
}

resource "aws_lambda_event_source_mapping" "del_es" {
  event_source_arn            = aws_sqs_queue.del.arn
  function_name               = aws_lambda_function.del_worker.arn
  batch_size                  = 5
  maximum_batching_window_in_seconds = 2
  function_response_types     = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------- STEP FUNCTIONS (Option A) ----------------
resource "aws_iam_role" "sfn_exec" {
  name = "kendra-sfn-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = "sts:AssumeRole", Effect = "Allow",
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "sfn_invoke_lambda" {
  name = "sfn-invoke-lambda"
  role = aws_iam_role.sfn_exec.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect="Allow", Action=["lambda:InvokeFunction"], Resource="*" },
      { Effect="Allow", Action=["sns:Publish"], Resource= var.sns_topic_arn != null ? var.sns_topic_arn : "*" }
    ]
  })
}

data "template_file" "asl" {
  template = file("${path.module}/../state_machines/orchestrator.asl.json")
  vars = {
    LoadManifestLambdaArn      = "REPLACE_ME"
    ListBatchesLambdaArn       = "REPLACE_ME"
    ProcessBatchLambdaArn      = aws_lambda_function.process_batch.arn
    AggregateFailuresLambdaArn = "REPLACE_ME"
    SnsTopicArn                = var.sns_topic_arn != null ? var.sns_topic_arn : "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dummy"
  }
}

resource "aws_sfn_state_machine" "kendra_docs" {
  name       = "kendra-docs-orchestrator"
  role_arn   = aws_iam_role.sfn_exec.arn
  definition = data.template_file.asl.rendered
}

# ---------------- DYNAMODB TABLES (optional but recommended) ----------------
resource "aws_dynamodb_table" "processed_docs" {
  name         = "kendra-processed-docs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "documentId"
  range_key    = "sourcePrefix"

  attribute { name = "documentId";  type = "S" }
  attribute { name = "sourcePrefix"; type = "S" }

  point_in_time_recovery { enabled = true }
}

resource "aws_dynamodb_table" "ingestion_locks" {
  name         = "kendra-ingestion-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "jobId"
  attribute { name = "jobId"; type = "S" }
}

resource "aws_dynamodb_table" "ingestion_jobs" {
  name         = "kendra-ingestion-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "jobId"
  attribute { name = "jobId"; type = "S" }
}

# ---------------- VARIABLES & OUTPUTS are in variables.tf / outputs.tf ----------------

# ------ SFN Starter Lambda (S3 manifest -> StartExecution) ------
resource "aws_lambda_function" "sfn_starter" {
  function_name = "kendra-sfn-starter"
  role          = aws_iam_role.lambda_exec.arn
  architectures = [local.lambda_arch]
  handler       = "sfn_starter.handler"
  runtime       = local.runtime
  filename      = "${path.module}/../lambdas_zips/sfn_starter.zip"
  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.kendra_docs.arn
    }
  }
}

# Allow starter to start the state machine
resource "aws_iam_role_policy" "starter_sfn_start" {
  name = "starter-sfn-start"
  role = aws_iam_role.lambda_exec.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Effect="Allow", Action=["states:StartExecution"], Resource=aws_sfn_state_machine.kendra_docs.arn },
      { Effect="Allow", Action=["s3:GetObject","s3:ListBucket"], Resource=[
        "arn:aws:s3:::${var.ingestion_bucket}",
        "arn:aws:s3:::${var.ingestion_bucket}/*"
      ]}
    ]
  })
}

# EventBridge rule: S3 Object Created for manifest.json in ingestion bucket
resource "aws_cloudwatch_event_rule" "manifest_created" {
  name = "kendra-manifest-created"
  event_pattern = jsonencode({
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": { "name": [var.ingestion_bucket] },
      "object": { "key": [{ "suffix": "manifest.json" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "manifest_to_starter" {
  rule      = aws_cloudwatch_event_rule.manifest_created.name
  target_id = "lambda-sfn-starter"
  arn       = aws_lambda_function.sfn_starter.arn
}

resource "aws_lambda_permission" "allow_events_to_invoke_starter" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sfn_starter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.manifest_created.arn
}
