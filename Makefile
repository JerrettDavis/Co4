install:
	python -m pip install -e '.[test]'
test:
	pytest -q
check:
	python -m compileall -q src
build:
	python -m pip wheel --no-deps --wheel-dir dist .
demo:
	PYTHONIOENCODING=utf-8 PYTHONUTF8=1 co4 demo
