# AGENTS.md

## Security

* Never commit or expose secrets, credentials, tokens, or personal data.
* Never use production data or credentials for development or testing.
* Never modify production or external systems without explicit user confirmation.
* Never perform destructive operations without explicit user confirmation.
* If security implications are unclear, stop and explain the uncertainty.

## APIs and Network Access

* Never make requests to APIs, services, or network endpoints without explicit
  user confirmation immediately before execution. Each request requires
  separate confirmation.
* Do not test integrations against production APIs.
* Never use discovered credentials, API keys, tokens, cookies, or environment
  variables without explicit user confirmation.

## Communication

* Always use English for code, identifiers, comments, and technical
  documentation.
