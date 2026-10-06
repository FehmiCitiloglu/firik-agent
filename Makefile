.PHONY: install setup test test-ui clean run commands ui gui help

ifeq ($(OS),Windows_NT)
  VENV_BIN ?= .venv/Scripts
  EXE ?= .exe
else
  VENV_BIN ?= $(if $(wildcard .venv/Scripts),.venv/Scripts,.venv/bin)
  EXE ?= $(if $(wildcard .venv/Scripts),.exe,)
endif

help:
	@echo "Firik Agent — Make Commands:"
	@echo "  make install   - Run installer (sets up .venv, deps, and commands)"
	@echo "  make test      - Run full pytest test suite"
	@echo "  make test-ui   - Run UI unit tests"
	@echo "  make run       - Launch interactive firik-agent REPL"
	@echo "  make commands  - List all available slash commands"
	@echo "  make ui        - Launch desktop UI application (GUI)"
	@echo "  make gui       - Alias for make ui"
	@echo "  make clean     - Remove caches and build artifacts"

install:
	@python3 install.py || python install.py

setup: install

test:
	@$(VENV_BIN)/pytest$(EXE)

test-ui:
	@$(VENV_BIN)/pytest$(EXE) tests/test_ui*.py

run:
	@$(VENV_BIN)/firik-agent$(EXE)

commands:
	@$(VENV_BIN)/firik-agent$(EXE) --commands

ui:
	@$(VENV_BIN)/firik-gui$(EXE) $(ARGS)

gui: ui

clean:
	rm -rf .pytest_cache build dist *.egg-info __pycache__ */__pycache__ */*/__pycache__
