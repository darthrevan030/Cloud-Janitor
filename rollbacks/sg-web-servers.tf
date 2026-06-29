# Rollback: Restore original 0.0.0.0/0 rule on sg-web-servers port 22
resource "aws_security_group_rule" "restore_sg_web_servers_port_22" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = "sg-web-servers"
  description       = "Kiro-Janitor: Rollback — restored original open rule"

  tags = {
    ManagedBy    = "Kiro-Janitor"
    Environment  = var.environment
    RemediatedAt = timestamp()
    RollbackRef  = "rollbacks/sg-web-servers.tf"
  }
}