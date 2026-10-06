.PHONY: install setup test test-ui clean run commands ui gui help

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
	@.venv/bin/pytest || .venv/Scripts/pytest.exe

test-ui:
	@.venv/bin/pytest tests/test_ui*.py || .venv/Scripts/pytest.exe tests/test_ui*.py

run:
	@.venv/bin/firik-agent || .venv/Scripts/firik-agent.exe

commands:
	@.venv/bin/firik-agent --commands || .venv/Scripts/firik-agent.exe --commands

ui:
	@.venv/bin/firik-gui $(ARGS) || .venv/Scripts/firik-gui.exe $(ARGS)

gui: ui

clean:
	rm -rf .pytest_cache build dist *.egg-info __pycache__ */__pycache__ */*/__pycache__
