"""Build the distributable zip for the 5-5 clean-machine install test.

    python tools/build-dist.py            # build to output/
    python tools/build-dist.py --verify   # build, then re-open and check it

WHAT SHIPS. 173 files, ~1.8 MB raw / 0.6 MB zipped (P2-R R4 measurement). Everything except the things a clean machine must
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
import tempfile
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
    if rel.name in EXCLUDE_NAMES:
        return True
    # P2-R R4: screenshots at the repo root, in tools/, and in stitch-design/ are
    # documentation/test artefacts, not app assets. Only frontend/** PNGs may ship.
    if rel.suffix.lower() == ".png" and (not rel.parts or rel.parts[0] != "frontend"):
        return True
    return False


def build(out_dir: pathlib.Path | None = None):
    out = pathlib.Path(out_dir) if out_dir is not None else OUT
    out.mkdir(exist_ok=True)
    version = (REPO / 'VERSION').read_text(encoding='utf-8').strip() \
        if (REPO / 'VERSION').exists() else 'dev'
    dest = out / f'GoClaudaddy-{version}.zip'
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


def check_archive(dest: pathlib.Path) -> int:
    """P2-R R4 check, run against an archive built to a TEMP path (never output\\).

    Lists every entry, then asserts:
      - `Launch GoClaudaddy.bat` and `tools/add-user-path.ps1` are present
      - no `*.png` entry lives outside `frontend/`
    and reports the uncompressed size carried under `legacy/` (kept by decision).
    """
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        infos = {i.filename: i for i in z.infolist()}

    print(f'\n=== p2-r check: {dest.name} ({len(names):,} entries) ===')
    bad = 0
    for nm in sorted(names):
        print(f'  {nm}')

    missing = [r for r in ('Launch GoClaudaddy.bat', 'tools/add-user-path.ps1') if r not in names]
    if missing:
        bad += 1
        print(f'  MISSING required entries: {missing}')
    else:
        print('  Launch GoClaudaddy.bat + tools/add-user-path.ps1 present')

    png_outside = sorted(
        nm for nm in names
        if nm.lower().endswith('.png') and not nm.startswith('frontend/')
    )
    if png_outside:
        bad += 1
        print(f'  PNG OUTSIDE frontend/ SHIPPED: {png_outside}')
    else:
        print('  no *.png outside frontend/')

    legacy_names = sorted(nm for nm in names if nm.startswith('legacy/'))
    legacy_bytes = sum(infos[nm].file_size for nm in legacy_names)
    print(f'  legacy/ carried: {len(legacy_names):,} entries · '
          f'{legacy_bytes:,} bytes uncompressed ({legacy_bytes / 1024:.1f} KiB)')

    print(f'\n{"P2-R CHECK PASS" if bad == 0 else "P2-R CHECK FAIL"} ({bad} problem(s))')
    return bad


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--check', action='store_true',
                    help='build to a TEMP dir and assert zip hygiene (never touches output\\)')
    a = ap.parse_args()
    if a.check:
        tmp_out = pathlib.Path(tempfile.mkdtemp(prefix='goclaudaddy-dist-check-'))
        d = build(tmp_out)
        sys.exit(check_archive(d))
    d = build()
    sys.exit(verify(d) if a.verify else 0)
