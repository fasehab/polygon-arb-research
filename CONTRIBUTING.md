# Contributing

Issues and PRs are welcome for deterministic tests, better pool decoding, correct transaction-boundary replays, token risk screening, historical datasets and state/fee accounting.

Please provide a reproducible block hash, addresses, token decimals, fee tier, exact input size, read-only trace or anonymized evidence and known limitations. Avoid embedding secrets, uncited profitability claims or live-signing changes. New execution capabilities require a separate design proposal and independent review, not a hidden default behavior.

All PRs must pass Python tests and release guard. Rust/Solidity modules imported from VPS must pass their own CI/builds before their status is upgraded in the README.
