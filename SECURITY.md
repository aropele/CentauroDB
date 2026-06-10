# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.9.x   | yes       |
| < 0.9   | no        |

Pre-1.0, only the latest minor release receives security fixes.

## Reporting a vulnerability

**Don't open a public GitHub issue for security problems.**

Use GitHub's private vulnerability reporting on this repository:
[Security → Report a vulnerability](https://github.com/aropele/centaurodb/security/advisories/new).
The report reaches the maintainer privately.

What to include:

- a minimal reproduction (model definitions plus the calls that trigger
  the issue);
- the impact you believe it has (data loss, SQL injection, code
  execution, …);
- the centaurodb version and the backend (SQLite / PostgreSQL) involved.

This is a single-maintainer project: expect an acknowledgement within a
few days, not hours. Coordinated disclosure is preferred for anything
that could lead to data loss or code execution — please allow a fix to
land before publishing details.

## Scope notes

- CentauroDB builds its SQL from identifier-validated names and
  parameterized values. `sql_select()` and `sql_execute()` run
  caller-supplied SQL by design — injection through strings your own
  application assembles is outside the library's threat model.
- Database credentials live in the connection URL passed to `Engine`;
  the library does not store or log them.
