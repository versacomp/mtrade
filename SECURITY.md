# Security Policy

MTrade connects to live financial markets and can place real orders on your brokerage account. Security is treated as a first-class concern. Please read this document carefully.

---

## Supported Versions

Only the latest release receives security patches. We strongly recommend always running the most recent version.

| Version | Supported |
|---------|-----------|
| 0.0.1 (latest) | ✅ |

---

## Credential and Secret Handling

MTrade never stores your tastytrade username or password. All secrets are loaded exclusively from a local `.env` file that is listed in `.gitignore` and must never be committed to source control.

| Secret | Storage | Transmitted to |
|--------|---------|----------------|
| Username / password | Not stored — used once per session | tastytrade API only (TLS) |
| OAuth client ID / secret | `.env` file only | tastytrade OAuth endpoint only (TLS) |
| OAuth refresh token | `.env` file only | tastytrade OAuth endpoint only (TLS) |
| Session token | In-memory only | tastytrade REST API (TLS) |
| DXLink streaming token | In-memory only | DXLink WebSocket (TLS) |

**All communication with the tastytrade API is over HTTPS/WSS (TLS 1.2+).** No credentials are logged, displayed in the UI, or written to any cache file.

### Protecting your `.env` file

- Set restrictive file permissions: `chmod 600 .env` (macOS/Linux)
- Never share or commit `.env`; use `.env.example` as the template
- Rotate all credentials immediately if you suspect exposure
- If `.env` was accidentally committed, rotate credentials and use `git filter-repo` to purge the history before making the repository public

---

## Live Trading Risk

The live trading feature submits **real orders** to your brokerage account. Treat it with the same diligence as any trading system:

- Enable live trading only when you fully understand the active strategy and filters
- Always test thoroughly in the **sandbox environment** before switching to production
- The live trading toggle requires explicit confirmation before activation
- There is no automatic kill switch for open positions — use the tastytrade platform directly to cancel orders or close positions if needed
- MTrade is not a regulated trading system; use it at your own risk (see `DISCLAIMER` in README)

---

## Scope of Security Concerns

When reporting, please consider the following risk categories relevant to a financial trading tool:

| Category | Examples |
|----------|---------|
| **Credential exposure** | Secrets logged, written to disk unencrypted, or transmitted insecurely |
| **Unauthorised order placement** | A bug that places orders without explicit user action |
| **Injection / RCE** | Command injection via symbol input, WebSocket message parsing, or cached JSON |
| **Dependency vulnerabilities** | Known CVEs in `flet`, `requests`, `websockets`, or `python-dotenv` |
| **Privilege escalation** | Any path that allows elevated API access beyond what the user configured |
| **Data integrity** | Tampering with cached candles or sim trade history to manipulate back-test results |

---

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Report vulnerabilities privately by emailing:

**security@versacomputer.com**

Include in your report:
1. A clear description of the vulnerability and its potential impact
2. Steps to reproduce or a proof-of-concept
3. The version of MTrade affected
4. Any suggested mitigations if you have them

You can expect an acknowledgement within **48 hours** and a status update within **7 days**. We will coordinate a fix and responsible disclosure timeline with you before any public announcement.

---

## Dependency Updates

Keep dependencies up to date to minimise exposure to known CVEs:

```bash
pip list --outdated
pip install --upgrade flet requests websockets python-dotenv
```

We recommend pinning dependencies to known-good versions in production deployments and reviewing `requirements.txt` after each upgrade.

---

## Out of Scope

- Vulnerabilities in the tastytrade API or DXLink infrastructure itself (report those to tastytrade)
- Theoretical attacks that require physical access to the user's machine
- Social engineering attacks against tastytrade support
- Issues in the `.venv` or third-party packages that have already been publicly disclosed and have no available fix
