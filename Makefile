.PHONY: help infra-check up down restart ps logs clean

COMPOSE := docker compose -f compose.yaml

help:
	@printf '%s\n' \
		'Available commands:' \
		'  make infra-check  Validate Docker Compose configuration' \
		'  make up           Start infrastructure services' \
		'  make down         Stop infrastructure services' \
		'  make restart      Restart infrastructure services' \
		'  make ps           Show infrastructure service status' \
		'  make logs         Follow infrastructure logs' \
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

clean:
	$(COMPOSE) down -v --remove-orphans
