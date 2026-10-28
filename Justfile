# Build dependencies are application-owned, never installed on reporting nodes.
set working-directory := '.'
# Prepared interpreter only: no implicit uv sync/download during build/configure.
python := env('PYTHON', '.venv/bin/python')

[default]
default:
    @just --list

# Explicit development dependency acquisition. No remote hosts or runtime config.
setup:
    uv sync --frozen --group build
    npm --prefix frontend ci

# Explicit, hash-verified PyPI acquisition for the declared offline center target.
prepare-wheels output=".build/wheels":
    {{python}} -m project_tools prepare-wheels --output '{{output}}'

frontend-build:
    npm --prefix frontend run build

assets: frontend-build
    {{python}} -m project_tools assets

run config db="data/monitor.sqlite3": assets
    {{python}} -m monitor --config '{{config}}' --db '{{db}}'

demo db="data/monitor-demo.sqlite3": assets
    {{python}} -m monitor --demo --db '{{db}}'

configure nodes="examples/nodes.json" url="http://127.0.0.1:8080/api/agent/report" output="config/local":
    {{python}} -m project_tools configure --nodes '{{nodes}}' --url '{{url}}' --output '{{output}}'

# Offline assembly; prepare-wheels is intentionally NOT an implicit prerequisite.
build output="dist": frontend-build
    {{python}} -m project_tools build --output '{{output}}'

schemas:
    {{python}} -m project_tools schemas

test: assets
    {{python}} -m unittest discover -s backend/tests -v
    {{python}} -m unittest discover -s node_reporter/tests -v
    {{python}} -m unittest discover -s tests -v
    npm --prefix frontend test

frontend-test:
    npm --prefix frontend test

browser-test: assets
    npm --prefix frontend run test:browser
    npm --prefix frontend run test:detail

check:
    npm --prefix frontend run typecheck
    npm --prefix frontend run format:check
    node --check frontend/tests/browser-smoke.mjs
    node --check frontend/tests/workspace-navigation.mjs
    node --check frontend/tests/branding.mjs
    node --check frontend/tests/authentication.mjs
    node --check frontend/tests/frontend-detail.mjs
