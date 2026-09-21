# Contributing

Contributions are welcome through GitHub pull requests. By submitting a contribution, you agree that it is distributed under GPL-2.0-only with the rest of this project.

## Development rules

1. Create a focused feature or fix branch from `main`.
2. Keep changes minimal and preserve CLI, configuration, journal, and platform compatibility unless the pull request explicitly documents a breaking change.
3. Update `README.md` and tests when behavior changes.
4. Run `python3 -m unittest discover -v`, `python3 -m compileall -q .`, and `git diff --check`.
5. Complete a sensitive-information audit before commit, push, or publication.

## Test data

Only synthetic data is accepted. Use RFC 5737 documentation addresses, RFC 2544 benchmarking addresses, locally administered MAC addresses, protocol-required broadcast or multicast constants, and unmistakable placeholders such as `<token>` or `<server_path>`.

Do not submit real PCAP/PCAPNG files, IP or MAC identifiers, credentials, cookies, logs, configuration, recovery journals, host paths, private topology, or operator/customer data. Public Issue and pull-request discussions must follow the same rule.

## Pull requests

Describe the purpose, compatibility impact, validation evidence, privacy-audit result, and any remaining platform or real-device gate. A pull request must not claim a version is released before its formal Release exists.
