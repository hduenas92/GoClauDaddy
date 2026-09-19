"""Build the distributable zip for the 5-5 clean-machine install test.

    python tools/build-dist.py            # build to output/
    python tools/build-dist.py --verify   # build, then re-open and check it

WHAT SHIPS. 175 files, ~9 MB. Everything except the things a clean machine must
create for itself, because the entire point of 5-5 is to find out whether it
CAN. Shipping .venv would hide a missing dependency; shipping ~/.goclaudaddy
would hide a broken first-run path; shipping node_modules would suggest a
runtime Node dependency that does not exist (Node is test-only -- package.json
declares only @playwright/test as a devDependency, and the frontend is plain ES
modules served as-is with no build step).

THE GUARD THAT MATTERS. A zip is trivially easy to build wrong, and wrong here
means the test proves nothing: a stray .venv makes a dependency failure
invisible, and a missing backend/requirements.txt makes it fail for a reason
that has nothing to do with the app. So --verify re-opens the archive and
asserts, against the archive itself rather than the source tree:
  - every file the launcher needs is present
  - no excluded directory leaked in
  - the entry point the README now names is actually in there
An archive that passed the build but fails these is caught here, not on a
machine where diagnosing it is expensive.
"""
import argparse
import pathlib
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / 'output'

# Directories a clean machine must build for itself, or that are test-only.
EXCLUDE_DIRS = {
    '.venv', 'node_modules', '.git', '__pycache__', '.pytest_cache',
    '.ruff_cache', 'output', '.claude', '.deepcode', '.agents',
}
EXCLUDE_SUFFIX = {'.pyc', '.pyo', '.bak', '.log'}
EXCLUDE_NAMES = {'last-run.json', 'baseline.json', '.DS_Store'}

# Present-or-the-archive-is-broken. Named explicitly rather than derived, so a
# refactor that moves one of these fails the build instead of shipping quietly.
REQUIRED = [
    'Launch GoClaudaddy.bat',            # the entry point README now names
    'README.md',
    'backend/run.py',
    'backend/requirements.txt',
    'backend/app/main.py',
    'backend/app/config.py',
    'backend/app/startup_check.py',
    'backend/app/watchdog.py',
    'backend/app/db/schema.sql',
    'backend/app/db/migrations.py',
    'frontend/index.html',
    'frontend/static/js/main.js',
    'frontend/static/css/base.css',
]


def should_skip(rel: pathlib.PurePath) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return rel.name in EXCLUDE_NAMES


def build():
    OUT.mkdir(exist_ok=True)
    version = (REPO / 'VERSION').read_text(encoding='utf-8').strip() \
        if (REPO / 'VERSION').exists() else 'dev'
    dest = OUT / f'GoClaudaddy-{version}.zip'
    if dest.exists():
        dest.unlink()

    n = total = 0
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(REPO.rglob('*')):
            if not p.is_file():
                continue
            rel = p.relative_to(REPO)
            if should_skip(rel):
                continue
            z.write(p, rel.as_posix())
            n += 1
            total += p.stat().st_size

    print(f'built {dest.name}')
    print(f'  {n:,} files · {total / 1024 / 1024:.1f} MB raw · '
          f'{dest.stat().st_size / 1024 / 1024:.1f} MB zipped')
    return dest


def verify(dest: pathlib.Path) -> int:
    """Check the ARCHIVE, not the tree it came from."""
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())

    print(f'\n=== verifying {dest.name} ({len(names):,} entries) ===')
    bad = 0

    missing = [r for r in REQUIRED if r not in names]
    if missing:
        bad += 1
        print(f'  MISSING required entries: {missing}')
    else:
        print(f'  all {len(REQUIRED)} required entries present')

    leaked = sorted({
        part for nm in names for part in pathlib.PurePosixPath(nm).parts
        if part in EXCLUDE_DIRS
    })
    if leaked:
        bad += 1
        print(f'  EXCLUDED DIRECTORIES LEAKED IN: {leaked}')
        for nm in sorted(names):
            if any(p in EXCLUDE_DIRS for p in pathlib.PurePosixPath(nm).parts):
                print(f'      {nm}')
                break
    else:
        print('  no excluded directory leaked in')

    # A clean machine must build its own venv. If one shipped, a dependency
    # failure would be invisible and the whole test would pass for free.
    if any(nm.startswith('.venv/') for nm in names):
        bad += 1
        print('  A .venv SHIPPED — the install test would prove nothing')

    print(f'\n{"VERIFIED" if bad == 0 else "ARCHIVE IS NOT SHIPPABLE"} '
          f'({bad} problem(s))')
    return bad


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    d = build()
    sys.exit(verify(d) if a.verify else 0)
