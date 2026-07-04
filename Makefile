.PHONY: demo demo-live seed clean

# Community edition — fixture-based scan, LocalStack for apply only
# Works without a LocalStack Pro token. ElastiCache apply won't work but everything else does.
demo:
	docker-compose up -d
	@echo "Waiting for LocalStack..."
	@i=0; while [ $$i -lt 30 ]; do \
		if curl -s http://localhost:4566/_localstack/health | grep -q '"ec2"'; then \
			echo " ready!"; break; \
		fi; \
		printf "."; \
		sleep 2; \
		i=$$((i + 1)); \
	done; \
	if [ $$i -eq 30 ]; then \
		echo "\nERROR: LocalStack failed to start within 60 seconds"; exit 1; \
	fi
	$(MAKE) clean
	bash scripts/seed-localstack.sh
	uv run cloud-janitor dashboard

# Live mode — full end-to-end against LocalStack Pro (no fixtures)
# Scan uses boto3 against seeded LocalStack resources, apply uses tflocal
# Requires LOCALSTACK_AUTH_TOKEN in .env
demo-live:
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
	$(MAKE) clean
	bash scripts/seed-localstack.sh
	JANITOR_BACKEND=aws AWS_ENDPOINT_URL=http://localhost:4566 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test uv run cloud-janitor dashboard

# Seed LocalStack with demo resources (run after container is healthy)
seed:
	bash scripts/seed-localstack.sh

# Clear all runtime output so the dashboard starts fresh
clean:
	@rm -f output/findings_store.json output/findings_store_*.json output/remediation.tf output/scan_history.json output/savings_ledger.json
	@rm -f output/rollbacks/*.tf
	@rm -f output/logs/audit.log output/logs/agent_reasoning.log
	@rm -f findings_store.json findings_store_*.json
