.PHONY: demo demo-live seed clean tf-init

# Community edition — fixture-based scan, LocalStack for apply only
# Works without a LocalStack Pro token. ElastiCache apply won't work but everything else does.
demo:
	docker-compose up -d
	@echo "Waiting for LocalStack..."
	@i=0; while [ $$i -lt 30 ]; do \
		if curl -s http://localhost:4566/_localstack/health | grep -q '"ec2": "available"'; then \
			echo " ready!"; break; \
		fi; \
		printf "."; \
		sleep 2; \
		i=$$((i + 1)); \
	done; \
	if [ $$i -eq 30 ]; then \
		echo "\nERROR: LocalStack failed to start within 60 seconds"; exit 1; \
	fi
	@rm -f output/findings_store.json output/findings_store_*.json output/remediation.tf output/scan_history.json output/savings_ledger.json output/approval_gates.json
	@rm -f output/rollbacks/*.tf
	@rm -f output/remediations/*.tf
	@rm -f output/policies/*.json
	@rm -f output/logs/audit.log output/logs/audit_*.log output/logs/agent_reasoning.log output/logs/scheduler.log output/logs/scheduler.log.*
	@rm -f findings_store.json findings_store_*.json
	uv run python scripts/seed_localstack.py
	uv run cloud-janitor dashboard

# Live mode — full end-to-end against LocalStack Pro (no fixtures)
# Scan uses boto3 against seeded LocalStack resources, apply uses tflocal
# Requires LOCALSTACK_AUTH_TOKEN in .env
# First run downloads the AWS provider (~300MB) — subsequent runs are instant.
demo-live: tf-init
	docker-compose -f docker-compose.yml -f docker-compose.pro.yml up -d
	@echo "Waiting for LocalStack Pro..."
	@i=0; while [ $$i -lt 30 ]; do \
		if curl -s http://localhost:4566/_localstack/health | grep -q '"elasticache": "available"'; then \
			echo " ready!"; break; \
		fi; \
		printf "."; \
		sleep 2; \
		i=$$((i + 1)); \
	done; \
	if [ $$i -eq 30 ]; then \
		echo "\nERROR: LocalStack Pro failed to start within 60 seconds"; exit 1; \
	fi
	@rm -f output/findings_store.json output/findings_store_*.json output/remediation.tf output/scan_history.json output/savings_ledger.json output/approval_gates.json
	@rm -f output/rollbacks/*.tf
	@rm -f output/remediations/*.tf
	@rm -f output/policies/*.json
	@rm -f output/logs/audit.log output/logs/audit_*.log output/logs/agent_reasoning.log output/logs/scheduler.log output/logs/scheduler.log.*
	@rm -f findings_store.json findings_store_*.json
	JANITOR_BACKEND=aws AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 JANITOR_DRY_RUN=1 uv run python scripts/seed_localstack.py
	JANITOR_BACKEND=aws AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 JANITOR_DRY_RUN=1 uv run cloud-janitor dashboard

# Pre-download the Terraform AWS provider (only downloads if not already cached)
tf-init:
	@echo "Ensuring Terraform AWS provider is cached..."
	@cd scripts/tf-provider-cache && terraform init -input=false -backend=false > /dev/null 2>&1 && echo "  ✓ AWS provider ready" || echo "  ⚠ terraform not found — approve will download provider on first use"

# Seed LocalStack with demo resources (run after container is healthy)
seed:
	uv run python scripts/seed_localstack.py

# Clear all runtime output so the dashboard starts fresh
clean:
	@rm -f output/findings_store.json output/findings_store_*.json output/remediation.tf output/scan_history.json output/savings_ledger.json output/approval_gates.json
	@rm -f output/rollbacks/*.tf
	@rm -f output/remediations/*.tf
	@rm -f output/policies/*.json
	@rm -f output/logs/audit.log output/logs/audit_*.log output/logs/agent_reasoning.log output/logs/scheduler.log output/logs/scheduler.log.*
	@rm -f findings_store.json findings_store_*.json
