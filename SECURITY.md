# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Scope

This is an offline research harness. It does not custody assets, sign
transactions, submit orders, or hold credentials. The strategy preview, bundle
planner, and self-trade guard never reach a settlement service.

The only network-using component is the Pinax downloader, which performs
read-only HTTP GET requests against public archives.

## Reporting a vulnerability

Please report suspected vulnerabilities privately instead of opening a public
issue. Open a
[security advisory](https://github.com/SrarrySky/QuietPool/security/advisories/new)
on GitHub, or contact the maintainers directly. Include:

- A description of the issue and its impact.
- Steps to reproduce.
- Any relevant logs, manifests, or hashes.

We will acknowledge within 7 days and aim to publish a fix with a changelog
entry. Please do not disclose the issue publicly before a fix is released.
