# Palette — common dev targets.
# Run `make help` for the menu.

.PHONY: help install dev docker-build docker-run clean

PORT ?= 18814

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install Python + Node deps
	pip install -r requirements.txt
	npm install

dev: ## Run the server on http://localhost:$(PORT)
	python app.py --port $(PORT)

docker-build: ## Build the container image
	docker build -t palette .

docker-run: ## Run the container, mapping $(PORT) -> 8080
	docker run --rm -p $(PORT):8080 -e RITS_API_KEY=$$RITS_API_KEY palette

clean: ## Remove pycache + workspace artifacts (preserves source)
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf workspace
