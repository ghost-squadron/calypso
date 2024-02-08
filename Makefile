stop:
	docker compose down

run: stop
	black backend
	isort backend
	docker compose up --build -d
	docker logs -f calypso-gsag-calypso-1

log:
	docker logs -f calypso-gsag-calypso-1

.PHONY: run stop log
.DEFAULT_GOAL := run
