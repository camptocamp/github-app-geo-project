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
	docker compose down --volume --remove-orphans
	docker compose up -d db
	docker compose run --rm tests wait-db
	# Create the tables
	docker compose run --no-TTY worker process-queue --exit-when-empty
	sleep 1
	# Run the application
	docker compose up -d
	# Get one success and one error job
	docker compose run --no-TTY worker send-event --application=test --event=test
	docker compose run --no-TTY worker process-queue --exit-when-empty
	# Get one new and one pending job
	docker compose run --no-TTY worker send-event --application=test --event=test
	docker compose run --no-TTY worker process-queue --only-one
	docker compose run --no-TTY worker process-queue --make-pending

.PHONY: tests
tests: ## Run the unit tests
tests:
	poetry install
	poetry run pytest -vv tests


.PHONY: acceptance-tests
acceptance-tests: ## Run the acceptance tests
acceptance-tests: run
	docker compose run --no-TTY tests pytest -vv /acceptance_tests
