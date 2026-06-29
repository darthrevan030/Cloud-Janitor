# Rollback: Restore ElastiCache cache-prod-legacy-01 from snapshot
resource "aws_elasticache_cluster" "restore_cache_prod_legacy_01" {
  cluster_id           = "cache-prod-legacy-01-restored"
  engine               = "redis"
  engine_version       = "7.0.7"
  node_type            = "cache.t3.medium"
  num_cache_nodes      = 1
  snapshot_name        = aws_elasticache_snapshot.pre_remediation_cache_prod_legacy_01.snapshot_name

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/cache-prod-legacy-01.tf"
  }
}