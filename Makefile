export DOCKER_BUILDKIT=1

.PHONY: help
help: ## Display this help message
	@echo "Usage: make <target>"
	@echo
	@echo "Available targets:"
	@grep --extended-regexp --no-filename '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "	%-20s%s\n", $$1, $$2}'

.PHONY: build
build: ## Build the acceptences test application Docker image
	docker compose build

.PHONY: run
run: ## Run the acceptences application Docker image
run: build
	docker compose up -d

.PHONY: tests
tests: ## Run the unit tests
tests:
	poetry install
	poetry run pytest -v tests


.PHONY: acceptance-tests
acceptance-tests: ## Run the acceptance tests
acceptance-tests: run
	echo docker compose exec -T application pytest -vv
