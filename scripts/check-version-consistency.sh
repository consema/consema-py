#!/usr/bin/env bash
# Version-consistency gate (tokio check-readme pattern) — the SINGLE shared
# copy executed by both ci-python.yml (check-version-consistency job) and
# release.yml (release path, wave-5: the release previously never re-ran the
# gate, so a bump that forgot the README/__init__/bug_report/LICENSE sync
# points could publish). Asserts FOUR sync points against the
# python/pyproject.toml version — (1) the README "Version:" line, (2)
# python/src/consema/__init__.py __version__, (3) the
# .github/ISSUE_TEMPLATE/bug_report.yml version literal (G73), and (4) the
# build-consumed license copy (P0 2026-08-14): the hatchling build ships
# python/LICENSE into the sdist and wheel (G95), so python/LICENSE must
# stay byte-identical to the repository-root LICENSE.
#
# The version source is read with tomllib from the [project] table
# (wave-5 2026-08-15: scope-anchored — a `version` key in any other TOML
# table cannot hijack the gate; the old `grep -m1 '^version'` first-match
# extraction matched any unscoped line).
set -euo pipefail
cd "$(dirname "$0")/.." # repository root (scripts/ -> root)

version="$(python - <<'PYEOF'
import pathlib, sys, tomllib
try:
    project = tomllib.loads(pathlib.Path('python/pyproject.toml').read_text(encoding='utf-8'))['project']
except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
    sys.exit(f'cannot read [project] from python/pyproject.toml: {error}')
try:
    print(project['version'])
except KeyError:
    sys.exit('python/pyproject.toml [project].version is missing')
PYEOF
)"
if [ -z "$version" ]; then
    echo "::error::no [project].version found in python/pyproject.toml"
    exit 1
fi

# Line-anchored match (not substring): a version bump from 1.0.0-rc.1 to
# 1.0.0 must not silently pass because the old string is a prefix of the
# new one — the version must be followed by end-of-line, whitespace or a
# full-width paren.
grep -qE "^Version: ${version//./\.}(\s|（|$)" README.md || {
    echo "::error::README.md does not declare the Version line 'Version: $version'"
    exit 1
}
grep -qF "__version__ = \"$version\"" python/src/consema/__init__.py || {
    echo "::error::python/src/consema/__init__.py does not declare __version__ = \"$version\""
    exit 1
}
# bug_report.yml version literal (G73, 2026-08-14): the template's
# environment-info line "version（当前 $version）" is a fourth sync
# location. The closing full-width paren anchors the match, so a bump from
# 1.0.0-rc.1 to 1.0.0 cannot pass on the old string being a prefix of the
# new one.
grep -qF "version（当前 $version）" .github/ISSUE_TEMPLATE/bug_report.yml || {
    echo "::error::.github/ISSUE_TEMPLATE/bug_report.yml does not declare the current version ($version)"
    exit 1
}
# License parity gate (P0, 2026-08-14): python/LICENSE is the file
# hatchling ships into the sdist and wheel (pyproject.toml
# [tool.hatch.metadata] license-files); it must never drift from the
# repository-root LICENSE.
if ! cmp -s LICENSE python/LICENSE; then
    echo "::error::python/LICENSE must be byte-identical to the repository-root LICENSE (the hatchling build ships python/LICENSE into the sdist and wheel)"
    exit 1
fi
echo "README / __init__ / bug_report.yml versions consistent with pyproject.toml: $version; python/LICENSE byte-identical to LICENSE"
