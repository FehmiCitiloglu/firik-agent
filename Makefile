.PHONY: install install-hybrid setup test test-ui test-hybrid clean run commands ui gui ui-hybrid hybrid-ui dev-hybrid help

ifeq ($(OS),Windows_NT)
  VENV_BIN ?= .venv/Scripts
  EXE ?= .exe
else
  VENV_BIN ?= $(if $(wildcard .venv/Scripts),.venv/Scripts,.venv/bin)
  EXE ?= $(if $(wildcard .venv/Scripts),.exe,)
endif

help:
	@echo "Firik Agent — Make Commands:"
	@echo "  make install        - Run Python installer (sets up .venv, CLI, and Python/Tk reference UI)"
	@echo "  make install-hybrid - Run hybrid installer (sets up .venv, Node deps, and TypeScript/Electron UI)"
	@echo "  make test           - Run full pytest test suite"
	@echo "  make test-ui        - Run Python UI unit tests"
	@echo "  make test-hybrid    - Run TypeScript check, Vitest, and desktop RPC tests"
	@echo "  make run            - Launch interactive firik-agent REPL"
	@echo "  make commands       - List all available slash commands"
	@echo "  make ui             - Launch Python/Tk reference UI (GUI)"
	@echo "  make gui            - Alias for make ui"
	@echo "  make ui-hybrid      - Build and launch TypeScript/Electron hybrid UI"
	@echo "  make dev-hybrid     - Launch TypeScript/Electron hybrid UI with Vite dev server"
	@echo "  make clean          - Remove caches and build artifacts"

install:
	@./install.sh

install-hybrid:
	@./install.sh --hybrid

setup: install

test:
	@$(VENV_BIN)/pytest$(EXE)

test-ui:
	@$(VENV_BIN)/pytest$(EXE) tests/test_ui*.py

test-hybrid:
	@npm run desktop:check
	@npm run desktop:test
	@$(VENV_BIN)/python$(EXE) -m pytest -q tests/test_desktop_rpc.py

run:
	@$(VENV_BIN)/firik-agent$(EXE)

commands:
	@$(VENV_BIN)/firik-agent$(EXE) --commands

ui:
	@$(VENV_BIN)/firik-gui$(EXE) $(ARGS)

gui: ui

ui-hybrid:
	@npm run desktop:build
	@npm run desktop:start

hybrid-ui: ui-hybrid

dev-hybrid:
	@npm run desktop:dev

clean:
	rm -rf .pytest_cache build dist *.egg-info __pycache__ */__pycache__ */*/__pycache__
