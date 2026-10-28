# Standalone application environment, owned by uv rather than the Ansible project.
set working-directory := '.'
python := env('PYTHON', 'uv run --frozen python')

[default]
default:
    @just --list

run config db="data/monitor.sqlite3":
    {{python}} -m monitor --config '{{config}}' --db '{{db}}'

demo db="data/monitor-demo.sqlite3":
    {{python}} -m monitor --demo --db '{{db}}'

configure nodes="examples/nodes.json" url="http://127.0.0.1:8080/api/agent/report" output="config/local":
    {{python}} -m project_tools configure --nodes '{{nodes}}' --url '{{url}}' --output '{{output}}'

build output="dist":
    {{python}} -m project_tools build --output '{{output}}'

test:
    {{python}} -S -m unittest discover -s tests -v

browser-test:
    uv run --frozen node tests/browser-smoke.mjs

check:
    node --check monitor/static/app.js
    node --check monitor/static/appearance.js
    node --check tests/browser-smoke.mjs
    node --check tests/workspace-navigation.mjs
    node --check tests/branding.mjs
    node --check tests/authentication.mjs
