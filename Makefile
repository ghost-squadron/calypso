run:
	docker compose down
	docker compose up --build -d
	docker logs -f calypso-gsag-calypso-1

.PHONY: run
.DEFAULT_GOAL := run
