# Contributing

This project does not accept external code contributions. Pull request creation
is restricted to the repository maintainer; forks are allowed but their changes
are not merged upstream.

## Issues

Bug reports and documentation problems are handled through GitHub Issues.
Include the version, operating system, architecture, command category, expected
result, actual result, and a minimal reproduction using synthetic data.

## Test data

Only synthetic data is accepted. Use RFC 5737 documentation addresses, RFC 2544
benchmarking addresses, locally administered MAC addresses, protocol-required
broadcast or multicast constants, and unmistakable placeholders such as
`<token>` or `<server_path>`.

Never post real PCAP/PCAPNG files, IP or MAC identifiers, credentials, cookies,
logs, configuration, recovery journals, host paths, private topology, or
operator/customer data in an Issue. Security vulnerabilities must follow
`SECURITY.md`, not a public Issue.

## Maintainer changes

Changes are made by the maintainer on focused branches and must pass
`python3 -m unittest discover -v`, `python3 -m compileall -q .`, `git diff
--check`, and a sensitive-information audit before reaching `main`. The
maintainer preserves the GPL-2.0-only license, CLI, configuration, journal, and
platform compatibility unless a change explicitly documents a breaking update.
