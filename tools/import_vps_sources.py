#!/usr/bin/env python3
"""Selectively import known Polygon bot source directories ON THE OWNER'S VPS.

Dry-run by default. Copies source code only; never raw archives, logs, JSON outputs,
secrets, binaries, Git history, or symlinks. Fail-closed on detected credentials.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from release_guard import inspect_text

DEFAULT_SOURCES = [
    ('/root/polygon_sniper', 'runtime/rust'),
    ('/root/polygon_step5_contract', 'contracts/balancer'),
    ('/root/polygon_step11_8/step11_8_polygon_tx_boundary_probe', 'research/historical/step11_8'),
    ('/root/polygon_step11_4/step11_4_polygon_swap_viability', 'research/historical/step11_4'),
]
ALLOWED_EXTENSIONS = {'.rs', '.sol', '.py', '.sql', '.md', '.toml', '.txt', '.sh', '.yml', '.yaml'}
ALLOWED_BASENAMES = {
    'Cargo.lock', 'Cargo.toml', 'Makefile', 'Dockerfile', 'foundry.toml',
    'remappings.txt', 'rust-toolchain', 'rust-toolchain.toml',
    'pyproject.toml', 'requirements.txt', 'pytest.ini', 'setup.cfg',
    'justfile', 'package.json', 'package-lock.json',
}
BLOCKED_DIRS = {
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
    'target', 'out', 'outputs', 'broadcast', 'cache', 'artifacts', 'logs',
    'build', 'dist', 'backups', 'snapshots', 'archive', 'archives', 'data',
    'private', 'secrets', 'credentials', 'keystore', 'keystores', 'lib',
}
BLOCKED_FILENAME_TOKENS = ('secret', 'credential', 'private', 'wallet', 'mnemonic', 'keystore', 'backup', '.bak', '.env', '.log', 'token.json')
MAX_FILE_SIZE = 1_000_000


def allow_file(relative: Path) -> bool:
    if any(p.lower() in BLOCKED_DIRS for p in relative.parts[:-1]):
        return False
    name = relative.name
    if any(token in name.lower() for token in BLOCKED_FILENAME_TOKENS):
        return False
    if name in ALLOWED_BASENAMES:
        return True
    if relative.suffix.lower() in ALLOWED_EXTENSIONS:
        return True
    # ABIs are optional and must be inspected independently (no free-form JSON dumps).
    if relative.suffix.lower() == '.json' and 'abi' in (p.lower() for p in relative.parts[:-1]):
        return True
    return False


def plan_import(src: Path, dest: Path, repo: Path):
    """Return [(file, relative_destination, content_digest)] or raise on unsafe input."""
    if not src.is_dir():
        return []
    if src.is_symlink() or repo == src or repo in src.parents or src in repo.parents:
        raise ValueError(f'Unsafe source or destination relationship: {src}')
    if dest.is_absolute() or '..' in dest.parts or not dest.parts:
        raise ValueError(f'Invalid destination: {dest}')
    planned = []
    errors = []
    for candidate in src.rglob('*'):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(src)
        if not allow_file(relative):
            continue
        if candidate.is_symlink():
            errors.append(f'symlink: {candidate}')
            continue
        if candidate.stat().st_size > MAX_FILE_SIZE:
            errors.append(f'oversize: {candidate}')
            continue
        blob = candidate.read_bytes()
        if b'\0' in blob:
            errors.append(f'binary: {candidate}')
            continue
        bad = inspect_text(blob.decode('utf-8', errors='replace'))
        if bad:
            errors.append(f'{candidate}: {", ".join(bad)}')
            continue
        planned.append((candidate, dest / relative, hashlib.sha256(blob).hexdigest()))
    if errors:
        raise ValueError('Unsafe source(s) detected; NO copying performed:\n' + '\n'.join(errors))
    return sorted(planned, key=lambda item: str(item[1]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='Actually copy (default previews only)')
    p.add_argument('--source', action='append', metavar='ABS_SOURCE:DEST_SUBDIR',
                   help='Override defaults. Can repeat. Destination must be relative to repo.')
    p.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    raw_sources = DEFAULT_SOURCES if not args.source else []
    for spec in args.source or []:
        if ':' not in spec:
            p.error('Use --source ABS_SOURCE:DEST_SUBDIR')
        source, target = spec.rsplit(':', 1)
        if not source.startswith('/'):
            p.error('Source must be absolute')
        raw_sources.append((source, target))
    all_planned = []
    for path, dest in raw_sources:
        src = Path(path).resolve()
        items = plan_import(src, Path(dest), repo)
        print(f'{path} -> {dest}: {len(items)} selected source files' + (' (not present)' if not Path(path).is_dir() else ''))
        all_planned.extend(items)
    if not all_planned:
        print('No sources found. Run this on the original VPS or override --source.')
        return 2
    destinations = [str(d) for _, d, _ in all_planned]
    if len(destinations) != len(set(destinations)):
        raise ValueError('Conflicting destination paths; no files copied.')
    # Preflight is complete before any source file copying occurs.
    if not args.apply:
        for source, relative, _ in all_planned[:25]:
            print(f'  PREVIEW {relative} <= {source}')
        if len(all_planned) > 25:
            print(f'  ...plus {len(all_planned)-25} more')
        print('Dry-run only; rerun with --apply after manual inspection.')
        return 0
    # Check ALL overwrite conflicts before the first copy: avoid partial imports.
    placeholders = {'runtime/rust/README.md', 'contracts/balancer/README.md'}
    for src, relative, digest in all_planned:
        dest = repo / relative
        if dest.exists() and dest.read_bytes() != src.read_bytes() and relative.as_posix() not in placeholders:
            raise ValueError(f'Would overwrite different existing file: {dest}')
    manifest = []
    for src, relative, digest in all_planned:
        dest = repo / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        manifest.append({'path': relative.as_posix(), 'sha256': digest})
    manifest_path = repo / 'docs' / 'import_manifest.json'
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({'status': 'COPIED_FROM_LOCAL_VPS_NOT_TESTED', 'files': manifest}, indent=2) + '\n')
    print(f'COPIED {len(manifest)} files. Review source, run tests/guard, and verify license before GitHub push.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as ex:
        print(f'ERROR: {ex}', file=sys.stderr)
        sys.exit(1)
