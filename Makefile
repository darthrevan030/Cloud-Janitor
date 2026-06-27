.PHONY: demo

# TF_CMD defaults to tflocal for LocalStack demo
# For real AWS: TF_CMD=terraform streamlit run app.py
demo:
	docker-compose up -d
	@echo "Waiting for LocalStack..."
	@i=0; while [ $$i -lt 30 ]; do \
		if curl -s http://localhost:4566/_localstack/health | grep -q '"ready"'; then \
			echo " ready!"; break; \
		fi; \
		printf "."; \
		sleep 2; \
		i=$$((i + 1)); \
	done; \
	if [ $$i -eq 30 ]; then \
		echo "\nERROR: LocalStack failed to start within 60 seconds"; exit 1; \
	fi
	streamlit run app.py
