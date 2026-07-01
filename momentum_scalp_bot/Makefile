# Convenience targets. `make setup` once, then `make paper` / `make testnet-bybit` / etc.
PY ?= python3
VENV := . .venv/bin/activate

.PHONY: help setup test backtest backtest-bybit paper paper-bybit testnet testnet-bybit live dashboard

help:
	@echo "targets: setup | test | backtest[-bybit] | paper[-bybit] | testnet[-bybit] | live | dashboard"

setup:
	$(PY) -m venv .venv
	$(VENV) && pip install --upgrade pip && pip install -r requirements.txt
	@[ -f .env ] || cp .env.example .env
	@echo "done. edit .env for keys, then: make paper"

test:
	$(VENV) && pytest

backtest:
	$(VENV) && bash scripts/start.sh backtest

backtest-bybit:
	$(VENV) && CONFIG=config.bybit.yaml bash scripts/start.sh backtest

paper:
	$(VENV) && bash scripts/start.sh paper

paper-bybit:
	$(VENV) && CONFIG=config.bybit.yaml bash scripts/start.sh paper

testnet:
	$(VENV) && bash scripts/start.sh testnet

testnet-bybit:
	$(VENV) && CONFIG=config.bybit.yaml bash scripts/start.sh testnet

live:
	$(VENV) && bash scripts/start.sh live

dashboard:
	$(VENV) && python -m momentum_scalp.dashboard --db data/bot.sqlite --serve 8787
