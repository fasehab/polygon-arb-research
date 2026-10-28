#!/usr/bin/env python3
"""Conservative public-release guard (heuristic only, never substitutes manual review).

Reject secret-shaped files and suspicious strings. It will NOT identify all secrets.
No network requests, wallet access, signing, broadcasting or Git operations.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import sys

BLOCKED_PARTS = {
    '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'target', 'out',
    'artifacts', 'outputs', 'logs', 'cache', 'node_modules', 'broadcast',
    'keystore', 'keystores', 'private', 'secrets', 'credentials',
}
BLOCKED_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.zip', '.gz', '.tar', '.db', '.sqlite', '.log', '.csv', '.bak', '.backup'}
DANGEROUS_NAME = re.compile(r'(^\.env(?:\.|$)|(?:private.?key|mnemonic|seed.?phrase|wallet.?json|\.secret|credentials?\.)|(?:^id_rsa$))', re.I)
SUSPICIOUS_CONTENT = [
    ('GitHub credential', re.compile(r'(?:ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('cloud access key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('Slack credential', re.compile(r'xox[baprs]-[A-Za-z0-9-]{15,}')),
    ('PEM private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ('literal private key assignment', re.compile(r'(?:private_key|signer_key|wallet_key)\s*[:=]\s*["\']?0x[0-9a-fA-F]{64}\b', re.I)),
    ('password in URL', re.compile(r'(?:https?|wss?)://[^\s/@:]+:[^\s/@]+@', re.I)),
    ('vendor RPC credential URL', re.compile(r'(?:https?|wss?)://[^\s\"\']*(?:alchemy\.com/v2/|infura\.io/v3/|quiknode\.pro/)[A-Za-z0-9_-]{16,}', re.I)),
    ('literal credential assignment', re.compile(r'\b(?:api_key|api_token|access_token|client_secret|rpc_api_key|secret_key)\s*[:=]\s*["\'][A-Za-z0-9/_+.-]{20,}["\']', re.I)),
]
ALLOWED_EXTRA = {'.gitignore', '.env.example'}


def inspect_text(text: str) -> list[str]:
    return [label for label, rule in SUSPICIOUS_CONTENT if rule.search(text)]


def scan_file(path: Path, root: Path) -> list[str]:
    relative = path.relative_to(root)
    problems = []
    if path.is_symlink():
        problems.append('symbolic link not allowed')
    if any(part.lower() in BLOCKED_PARTS for part in relative.parts[:-1]):
        problems.append('blocked directory')
    if path.name.lower() != '.env.example' and (DANGEROUS_NAME.search(path.name) or path.suffix.lower() in BLOCKED_SUFFIXES):
        problems.append('sensitive filename or non-source extension')
    if path.stat().st_size > 2_000_000:
        problems.append('file >2 MB requires manual assessment')
    try:
        b = path.read_bytes()
        if b'\0' in b:
            problems.append('binary file')
        else:
            problems.extend(inspect_text(b.decode('utf-8', errors='replace')))
    except OSError as ex:
        problems.append(f'unreadable: {ex}')
    return problems


def scan_tree(root: Path) -> dict[str, list[str]]:
    findings = {}
    for item in sorted(root.rglob('*')):
        if not item.is_file() and not item.is_symlink():
            continue
        # Ignore transient test caches or staged local .git history; must not be committed.
        if any(part.lower() in {'.git', '.venv', '__pycache__', '.pytest_cache'} for part in item.relative_to(root).parts):
            continue
        faults = scan_file(item, root)
        if faults:
            findings[str(item.relative_to(root))] = faults
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = scan_tree(args.root.resolve())
    if findings:
        for file, problems in findings.items():
            print(f'BLOCKED: {file} ({", ".join(problems)})')
        print('PUBLIC RELEASE NOT APPROVED. Remove/rotate credentials and review all files.')
        return 1
    print('Release guard: no detected sensitive files/strings. Manual review still mandatory.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
