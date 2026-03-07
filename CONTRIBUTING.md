# Contributing to MTrade

Thank you for your interest in contributing. MTrade is actively looking for developers who want to bring new strategy ideas, improve the existing signal pipeline, or extend the platform. This guide covers everything you need to get a pull request merged.

---

## Ways to Contribute

| Type | Examples |
|------|---------|
| **New strategy** | Alternative signal detection logic, new filter types, different entry/exit rules |
| **Bug fix** | Incorrect indicator calculation, UI rendering issue, data handling edge case |
| **Instrument support** | Adding new symbols to the futures registry with accurate pricing metadata |
| **Performance** | Chart rendering optimisation, stream throughput, caching improvements |
| **Documentation** | Clarifying strategy logic, improving setup instructions, adding inline comments |
| **Tests** | Unit tests for signal detection, indicator maths, trade management logic |

---

## Development Setup

```bash
git clone https://github.com/your-org/m-trade.git
cd m-trade

python -m venv .venv
.venv\Scripts\activate        # Windows
# or: source .venv/bin/activate  # macOS / Linux

pip install -r requirements.txt

cp .env.example .env
# Fill in sandbox credentials — never use production credentials during development
```

Run the app:

```bash
python main.py
```

---

## Contribution Workflow

1. **Open or find an issue** — check existing issues before starting work. For significant changes, open an issue to discuss the approach first.

2. **Fork the repository** and create a branch from `main`:
   ```bash
   git checkout -b feature/my-feature-name
   # or: git checkout -b fix/short-description
   ```

3. **Make your changes** following the code style guidelines below.

4. **Test your changes** against the sandbox environment. If you are adding strategy logic, include a brief back-test result in your PR description.

5. **Submit a pull request** against `main`. Fill in the PR template fully — incomplete PRs will not be reviewed.

---

## Code Style

- **Python 3.11+** — use modern type hints (`str | None`, `list[int]`, etc.)
- Follow existing naming conventions — `snake_case` for functions and variables, `UPPER_SNAKE` for module-level constants
- Keep functions focused — if a function exceeds ~40 lines, consider splitting it
- No external dependencies without prior discussion — keep `requirements.txt` minimal
- All secrets and credentials must come from environment variables via `config.py` — never hardcode URLs, tokens, or keys

---

## Adding a New Strategy

The signal pipeline is intentionally modular. To add an alternative strategy:

1. Add a new detection function alongside `detect_signals()` in `institutional_liquidity_view.py` (or a new module under `views/`)
2. Return a `list[Signal]` with the same `Signal` dataclass — this ensures compatibility with the existing chart overlay, trade management, and analysis view
3. Add a toggle in the legend row so the user can switch strategies at runtime without restarting the stream
4. Document the strategy logic in your PR description: premise, entry conditions, filter criteria, and a sample back-test result

---

## Commit Message Format

Use the imperative mood and keep the subject line under 72 characters:

```
Add RSI-momentum filter to swing signal detection
Fix candle timestamp alignment across DST boundary
Update MES tick size in futures registry
```

---

## Pull Request Checklist

Before submitting, confirm:

- [ ] Tested against sandbox — not production
- [ ] No credentials, `.env` values, or personal data in the diff
- [ ] `CHANGELOG.md` updated under an `[Unreleased]` section
- [ ] PR template filled in completely

---

## Reporting Bugs

Use the **Bug Report** issue template on GitHub. For security vulnerabilities, follow the [Security Policy](SECURITY.md) — do not open a public issue.

---

## Questions

Open a **Discussion** on GitHub for general questions, strategy ideas, or feedback that does not fit an issue or PR.
