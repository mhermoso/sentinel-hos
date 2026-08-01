# Contributing to Driver Compliance Watch (DCW)

Thank you for contributing! To maintain regulatory compliance, mathematical determinism, and high code quality, all contributions must follow these guidelines.

---

## 1. Branch Naming Conventions

All work must be developed in dedicated feature or fix branches using standard prefixes:

* `feature/` : New capabilities or domain additions (e.g., `feature/samsara-webhook-adapter`)
* `fix/`     : Bug fixes or precision calculation adjustments (e.g., `fix/split-sleeper-clock-reset`)
* `adr/`     : Architectural Decision Records additions or updates (e.g., `adr/006-event-store-indexing`)
* `refactor/`: Internal code improvements that do not alter deterministic calculation outputs

---

## 2. Commit Message Standard (Conventional Commits)

Commit messages must strictly follow the Conventional Commits specification:

* `feat(engine): add split-sleeper berth 7/3 evaluation logic`
* `fix(ingestion): correct timestamp truncation for geotab feeds`
* `docs(adr): record ADR-005 timezone policy`
* `test(engine): add 70h/8d rolling window edge cases`

---

## 3. Pull Request Requirements

Before submitting a Pull Request (PR):

1. **Verify All Tests Pass**: Run `make test` locally.
2. **Ensure Code Quality & Types**: Run `make lint` and fix all Ruff, Mypy, and Bandit errors.
3. **Format Code**: Run `make format`.
4. **Maintain Determinism**: Any changes to `app/domains/engine/` must include corresponding unit test cases covering edge conditions.
