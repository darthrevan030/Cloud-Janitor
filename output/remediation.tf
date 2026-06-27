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