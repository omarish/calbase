.PHONY: install-global test

install-global:
	uv tool install . --reinstall

test:
	PYTHONPATH=. uv run --extra test pytest tests
