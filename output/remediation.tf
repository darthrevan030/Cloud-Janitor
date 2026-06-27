# Remediation: EBS volume vol-0abc123def456789a — snapshot then destroy
resource "aws_ebs_snapshot" "pre_remediation_vol_0abc123def456789a" {
  volume_id   = "vol-0abc123def456789a"
  description = "Pre-remediation snapshot for vol-0abc123def456789a"

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/vol-0abc123def456789a.tf"
  }
}

resource "null_resource" "destroy_vol_0abc123def456789a" {
  depends_on = [aws_ebs_snapshot.pre_remediation_vol_0abc123def456789a]

  provisioner "local-exec" {
    command = "aws ec2 delete-volume --volume-id vol-0abc123def456789a"
  }
}

# Remediation: Narrow sg-prod-redis port 6379 to VPC-only
data "aws_vpc" "current" {
  default = true
}

resource "aws_security_group_rule" "remediate_sg_prod_redis_port_6379" {
  type              = "ingress"
  from_port         = 6379
  to_port           = 6379
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.current.cidr_block]
  security_group_id = "sg-prod-redis"
  description       = "Kiro-Janitor: Narrowed from 0.0.0.0/0 to VPC CIDR"
}

# Remediation: ElastiCache cache-prod-legacy-01 — snapshot then delete
resource "aws_elasticache_snapshot" "pre_remediation_cache_prod_legacy_01" {
  cluster_id       = "cache-prod-legacy-01"
  snapshot_name    = "pre-remediation-cache-prod-legacy-01"
}

resource "null_resource" "destroy_cache_prod_legacy_01" {
  depends_on = [aws_elasticache_snapshot.pre_remediation_cache_prod_legacy_01]

  provisioner "local-exec" {
    command = "aws elasticache delete-cache-cluster --cache-cluster-id cache-prod-legacy-01 --final-snapshot-identifier final-cache-prod-legacy-01"
  }

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/cache-prod-legacy-01.tf"
  }
}