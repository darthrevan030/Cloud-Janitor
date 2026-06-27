# Infrastructure Remediation Standards

## Terraform Tag Requirements

Every generated resource block must include:
  ManagedBy    = "Kiro-Janitor"
  Environment  = var.environment
  RemediatedAt = timestamp()
  RollbackRef  = "rollbacks/<resource_id>.tf"

## EBS Volume Rules

- Unattached > 7 days: FLAG (severity=MEDIUM)
- Unattached > 30 days: REMEDIATE
  1. aws_ebs_snapshot_copy (snapshot first, depends_on enforced)
  2. aws_ebs_volume destroy
  Rollback: aws_ebs_volume restore from snapshot ARN

## Security Group Rules

- Never delete a Security Group — always narrow CIDR
- Replace 0.0.0.0/0 ingress with data.aws_vpc.current.cidr_block
- Sensitive ports (VPC-only required): 22, 3306, 5432, 6379, 27017
- Rollback: restore original 0.0.0.0/0 rule (stored in rollback HCL)

## ElastiCache Rules

- Idle > 30 days + no active connections: snapshot → delete
- Rollback: restore from snapshot, same node_type and engine version
- New clusters must have: encryption_at_rest=true, auth_token if public subnet

## Approval Gate Protocol

- Display dependency check result first
- Display remediation HCL diff
- Display rollback HCL alongside (not below)
- Require: "APPROVE <resource-id>" — exact match, case-sensitive
- Reject and re-prompt on any other input
- Log approval with: timestamp, resource_id, approver, action

## Error Handling Rules

- Dependency found: surface warning, block remediation, suggest manual review
- terraform validate fails: surface error text, block approval prompt
- Approval string mismatch: display expected format, re-prompt (max 3 attempts)
- Rollback artifact missing: surface error, do not proceed
