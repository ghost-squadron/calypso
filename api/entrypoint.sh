#!/bin/sh
uvicorn app:app --host 0.0.0.0 --port 80 --use-colors --log-config log_conf.json