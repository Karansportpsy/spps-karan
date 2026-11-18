@echo off
rem Runs the automated checks (they use made-up test data in a temporary database, never your Supabase data).
cd /d "%~dp0"
if not exist .venv-tests\Scripts\python.exe (py -3 -m venv .venv-tests 2>NUL || python -m venv .venv-tests)
.venv-tests\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements-development.txt
.venv-tests\Scripts\python.exe -m playwright install chromium
set "MODEL_C_INIT_DB=false"
set "MODEL_C_SEED_DEMO=false"
.venv-tests\Scripts\python.exe -m pytest -q tests tests_v1 tests_v2
pause
