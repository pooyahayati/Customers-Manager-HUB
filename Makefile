.PHONY: help infra-check up down restart ps logs migrate clean

COMPOSE := docker compose -f compose.yaml

help:
	@printf '%s\n' \
		'Available commands:' \
		'  make infra-check  Validate Docker Compose configuration' \
		'  make up           Start application services' \
		'  make down         Stop application services' \
		'  make restart      Restart application services' \
		'  make ps           Show application service status' \
		'  make logs         Follow application logs' \
		'  make migrate      Apply database migrations explicitly' \
		'  make clean        Stop services and remove project volumes'

infra-check:
	$(COMPOSE) config --quiet

up: infra-check
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart: down up

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

migrate:
	$(COMPOSE) run --rm api alembic upgrade head

clean:
	$(COMPOSE) down -v --remove-orphans
