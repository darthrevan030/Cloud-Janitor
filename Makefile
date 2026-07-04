.PHONY: demo demo-pro seed

# Community edition — works without a LocalStack auth token
# ElastiCache approve/rollback won't work, but audit + dashboard are fully functional
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
	bash scripts/seed-localstack.sh
	uv run cloud-janitor dashboard

# Pro edition — requires LOCALSTACK_AUTH_TOKEN in .env
# Full demo including ElastiCache snapshot-and-delete
demo-pro:
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
	bash scripts/seed-localstack.sh
	uv run cloud-janitor dashboard

# Seed LocalStack with demo resources (run after container is healthy)
seed:
	bash scripts/seed-localstack.sh
