# Security policy

## Supported versions

Security fixes are made on the latest release line. Older builds may be asked to upgrade before a report is investigated.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting for the repository. Include affected versions, reproduction steps, impact, and any suggested mitigation. Avoid including private footage, project files, credentials, or model tokens.

Samosa binds its service to `127.0.0.1`. Changes that expose the service to another interface, weaken request validation, or accept untrusted paths require explicit security review.
