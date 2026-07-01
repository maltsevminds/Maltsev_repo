# Convenience targets. `make setup` once, then `make paper` / `make testnet-bybit` / etc.
PY ?= python3
VENV := . .venv/bin/activate

.PHONY: help setup test backtest paper testnet testnet-bybit live dashboard

help:
	@echo "targets: setup | test | backtest | paper | testnet | testnet-bybit | live | dashboard"

setup:
	$(PY) -m venv .venv
	$(VENV) && pip install --upgrade pip && pip install -r requirements.txt
	@[ -f .env ] || cp .env.example .env
	@echo "done. edit .env for keys, then: make paper"

test:
	$(VENV) && pytest

backtest:
	$(VENV) && bash scripts/start.sh backtest

paper:
	$(VENV) && bash scripts/start.sh paper

testnet:
	$(VENV) && bash scripts/start.sh testnet

testnet-bybit:
	$(VENV) && CONFIG=config.bybit.testnet.yaml bash scripts/start.sh testnet

live:
	$(VENV) && bash scripts/start.sh live

dashboard:
	$(VENV) && python -m momentum_scalp.dashboard --db data/bot.sqlite --serve 8787
