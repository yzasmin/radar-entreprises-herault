# Raccourcis du projet. `make aide` liste tout.
SHELL := /bin/bash
JOUR ?= $(shell date -d yesterday +%F 2>/dev/null || date -v-1d +%F)

.PHONY: aide installer tests style pile arret graphe diagnostic resultats figures terraform-valider nettoyer

aide: ## Liste les commandes
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

installer: ## Installe l'environnement Python (uv)
	python -m uv sync --frozen

tests: ## Tests unitaires (sans Airflow ni Docker)
	python -m uv run pytest -q

style: ## Verification de style
	python -m uv run ruff check src tests dags scripts

pile: ## Monte Airflow et LocalStack
	docker compose up -d --build

arret: ## Arrete la pile sans supprimer les volumes
	docker compose down

graphe: ## Execute reellement le graphe sur JOUR=AAAA-MM-JJ
	docker compose run --rm airflow-cli airflow dags test radar_entreprises_herault $(JOUR)

diagnostic: ## Dit si l'on parle a LocalStack ou au vrai AWS
	docker compose run --rm airflow-cli python -m radar.cli diagnostic

resultats: ## Exporte les chiffres publies dans results/
	AWS_ENDPOINT_URL=http://localhost:4566 python -m uv run python scripts/export_results.py --jour $(JOUR)

figures: ## Trace les graphiques du README et du teaser
	python -m uv run python scripts/figures.py --jour $(JOUR)

terraform-valider: ## Verifie l'infrastructure decrite en code, sans compte AWS
	cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate

nettoyer: ## Supprime la pile et ses volumes
	docker compose down -v
