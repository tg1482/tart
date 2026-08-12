# tart — live terminal artifacts

test:
	uv run --with-editable . --with pytest pytest tests/ -q

lint:
	uv run --with ruff ruff check --select F tartifacts/ tests/

install:
	uv tool install --force -e --with rich .

check: lint test

.PHONY: test lint install check
