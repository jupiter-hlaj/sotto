.PHONY: build test lint local deploy-dev logs-webhook

build:
	sam build --template backend/template.yaml --use-container

test:
	pytest backend/src/tests/ -v --tb=short

lint:
	ruff check backend/src/ && ruff format --check backend/src/ && bandit -r backend/src/ -ll

local:
	sam local start-api --template backend/template.yaml --env-vars local.env.json

deploy-dev:
	sam deploy --stack-name sotto-dev --parameter-overrides Environment=dev LogLevel=DEBUG

logs-webhook:
	aws logs tail /aws/lambda/sotto-ProviderWebhookHandler-dev --follow
