# Palette — common dev + deploy targets.
# Run `make help` for the menu.
#
# Code Engine targets shell out to scripts in deployment/; they read
# config from deployment/config.sh (override via env vars — see
# deployment/DEPLOYMENT.md).

.PHONY: help install dev docker-build docker-run clean \
        ce-build ce-push ce-buildpush ce-deploy ce-release

PORT ?= 18814

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install Python + Node deps
	pip install -r requirements.txt
	npm install

dev: ## Run the server on http://localhost:$(PORT)
	python app.py --port $(PORT)

# --- Local Docker (native arch) ---

docker-build: ## Build the container image for your local arch
	docker build -t palette .

docker-run: ## Run the local container, mapping $(PORT) -> 8080
	docker run --rm -p $(PORT):8080 -e RITS_API_KEY=$$RITS_API_KEY palette

# --- IBM Code Engine (linux/amd64) ---

ce-build: ## Cross-build linux/amd64 image and load it locally
	./deployment/build.sh

ce-push: ## Push the built image to IBM Container Registry
	./deployment/push.sh

ce-buildpush: ## Build linux/amd64 and push to ICR in one step
	./deployment/buildpush-ce.sh

ce-deploy: ## Create or update the Code Engine application
	./deployment/deploy.sh

ce-release: ce-buildpush ce-deploy ## Build, push, and deploy in sequence

# --- Housekeeping ---

clean: ## Remove pycache + workspace artifacts (preserves source)
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf workspace
