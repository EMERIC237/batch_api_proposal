output "state_machine_arn" { value = try(aws_sfn_state_machine.kendra_docs.arn, null) }
output "sqs_put_url"        { value = try(aws_sqs_queue.put.id, null) }
output "sqs_del_url"        { value = try(aws_sqs_queue.del.id, null) }
output "ddb_processed_docs" { value = try(aws_dynamodb_table.processed_docs.name, null) }
