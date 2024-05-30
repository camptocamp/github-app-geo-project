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
	${shell which docker} compose build

.PHONY: run
run: ## Run the acceptences application Docker image
run: build
	${shell which docker} compose down --volume --remove-orphans
	${shell which docker} compose up -d db
	${shell which docker} compose run --rm tests wait-db
	# Create the tables
	${shell which docker} compose run --no-TTY worker process-queue --exit-when-empty
	sleep 1
	# Run the application
	${shell which docker} compose up -d
	# Get one success and one error job
	${shell which docker} compose run --no-TTY worker send-event --application=test --event=test
	${shell which docker} compose run --no-TTY worker process-queue --exit-when-empty
	# Get one new and one pending job
	${shell which docker} compose run --no-TTY worker send-event --application=test --event=test
	${shell which docker} compose run --no-TTY worker process-queue --only-one
	${shell which docker} compose run --no-TTY worker process-queue --make-pending

.PHONY: tests
tests: ## Run the unit tests
tests:
	poetry install
	poetry run pytest -vv tests


.PHONY: acceptance-tests
acceptance-tests: ## Run the acceptance tests
acceptance-tests: run
	${shell which docker} compose run --no-TTY tests pytest -vv /acceptance_tests
