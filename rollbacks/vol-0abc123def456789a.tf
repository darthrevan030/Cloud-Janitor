# Rollback: Restore EBS volume vol-0abc123def456789a from snapshot
resource "aws_ebs_volume" "restore_vol_0abc123def456789a" {
  availability_zone = "us-east-1b"
  snapshot_id       = aws_ebs_snapshot.pre_remediation_vol_0abc123def456789a.id
  size              = 100
  type              = "gp3"

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/vol-0abc123def456789a.tf"
  }
}