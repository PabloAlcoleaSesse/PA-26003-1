# ==============================================================================
# Stock Screener & Fundamental Analysis Pipeline - Docker Automation Makefile
# ==============================================================================

.PHONY: help build up down restart logs init-db sync-universe fetch screen run-all clean

# Default target
help:
	@echo "Available Docker commands:"
	@echo "  make build          - Build the application Docker image"
	@echo "  make up             - Start PostgreSQL database in background"
	@echo "  make down           - Stop all containers"
	@echo "  make logs           - Stream container logs"
	@echo "  make init-db        - Run schema migrations inside Docker"
	@echo "  make sync-universe  - Discover US common stocks from SEC EDGAR"
	@echo "  make fetch          - Fetch fundamentals for stale tickers (default 100)"
	@echo "  make screen         - Execute multi-factor quantitative screen"
	@echo "  make run-all        - Execute full pipeline: init -> sync -> fetch -> screen"
	@echo "  make clean          - Stop containers and remove persistent database volume"

build:
	docker compose build

up:
	docker compose up -d db

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

init-db: up
	docker compose run --rm app init-db

sync-universe: up
	docker compose run --rm app sync-universe

fetch: up
	docker compose run --rm app fetch --workers 8 --limit 100

screen: up
	docker compose run --rm app screen --top 25

run-all: up
	docker compose run --rm app run-all --workers 8 --limit 100 --top 25

clean:
	docker compose down -v
