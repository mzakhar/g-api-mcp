# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report them via [GitHub's private vulnerability reporting](https://github.com/mzakhar/g-api-mcp/security/advisories/new).

Include:
- Description of the vulnerability and potential impact
- Steps to reproduce
- Any suggested fix, if you have one

You can expect an acknowledgment within 48 hours and a resolution or status update within 7 days.

## Scope

This project handles sensitive OAuth credentials and accesses Gmail, Google Calendar, Google Tasks, and Google Contacts on behalf of the user. Issues in the following areas are in scope:

- OAuth token handling (`auth_setup.py`, `src/g_api_mcp/auth.py`)
- Credential storage (Windows Credential Locker via `keyring`)
- Any path that could allow a third party to read or exfiltrate mail/calendar/contacts data

## Out of Scope

- Vulnerabilities in upstream dependencies (report those to the respective projects)
- Issues requiring physical access to the machine

## Security Design Notes

- `client_secrets.json` is gitignored and never committed
- The OAuth refresh token is stored in Windows Credential Locker (DPAPI-encrypted), not as a plaintext file
- `client_id` and `client_secret` are read from `client_secrets.json` on every token refresh — rotating the client secret in Google Cloud Console takes effect without re-authenticating
- All Google API calls are made directly from the local machine — no data passes through any third-party proxy
