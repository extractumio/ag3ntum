# Quality Grades

Updated: 2026-02-21 (initial — no baseline yet)

All values are TBD until the first full test run with coverage is completed.

## How to Establish Baseline

1. Run `./run.sh test --all` with coverage enabled
2. Run `./run.sh lint` for lint status
3. Fill in the table below with actual values
4. Set coverage targets at baseline + 5%, ratchet up over time

## Grades

| Domain | Test Coverage | Type Safety | Lint Clean | Doc Quality | Grade |
|--------|:----------:|:---------:|:--------:|:---------:|:-----:|
| src/core/ | — | partial (mypy) | — | yes | — |
| src/api/ | — | partial (mypy) | — | yes | — |
| src/services/ | — | none | — | yes | — |
| tools/ag3ntum/ | — | none | — | yes | — |
| web_terminal_client/ | — | strict TS | — | partial | — |
| prompts/ | N/A | N/A | N/A | yes | — |
