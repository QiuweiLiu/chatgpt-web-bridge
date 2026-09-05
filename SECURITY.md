# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.9.x   | :white_check_mark: |
| < 0.9   | :x:                |

## Reporting a Vulnerability

**Do not open a public issue** for security reports. Contact the maintainer
privately (see the repository owner profile) with:

- affected version (`bridge.py --version`) and environment (`doctor` output
  with private paths redacted, see below);
- steps to reproduce;
- impact assessment.

## Never Include in Any Report or Attachment

- cookies, session tokens, API keys, passwords, `.env` contents;
- account identifiers beyond what is strictly necessary;
- private ChatGPT conversation URLs or message contents;
- raw browser profiles (`--user-data-dir` contents);
- private files you uploaded through the bridge.

Redact freely — a report with placeholders (`<redacted>`) is preferred over
a complete-but-leaking one.

## In-Scope Examples

- send/upload/download reaching the wrong conversation;
- confirmation (`--confirm-*`) or model-gate bypass;
- session secret exfiltration through bridge outputs;
- escaping the loopback/CDP security boundary described in the README.
