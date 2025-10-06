variable "aws_region"      { type = string }
variable "ingestion_bucket" { type = string }
variable "reports_bucket"   { type = string }
variable "kendra_index_id"  { type = string }
variable "sns_topic_arn"    { type = string, default = null }
variable "deploy_option"    { type = string, default = "sfn" } # or "sqs"
