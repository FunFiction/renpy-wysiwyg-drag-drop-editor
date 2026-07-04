"""Test runner for the WYSIWYG editor.

Each directory under cases/ is a small, self-contained Ren'Py project with
a self-test label that exercises one area of the editor and writes its
findings to game/selftest_result.txt. This script copies every case into a
scratch build directory, drops the current editor file in, runs the game
unattended (a real Ren'Py window opens for a few seconds per case), and
checks the output against the case's expect.txt.

Usage:
    python run_tests.py [--sdk PATH] [--case NAME] [--keep]

expect.txt syntax (one directive per line, '#' starts a comment):
    RUNS <n>                     run the project n times (default 1);
                                 checks apply to the final run
    MATCH <regex>                selftest_result.txt must contain the regex
    NOMATCH <regex>              ... must not contain it
    FILEMATCH <relpath> <regex>  file under game/ must contain the regex
    FILENOMATCH <relpath> <regex>  ... must not contain it
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


def rmtree_retry(path, attempts=5):
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(1.0)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EDITOR = os.path.join(REPO, "game", "wysiwyg_editor.rpy")
DEFAULT_SDK = r"<your-renpy-sdk-path>"


def read_expectations(path):
    runs = 1
    checks = []
    with io.open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            head, _, rest = line.partition(" ")
            if head == "RUNS":
                runs = int(rest)
            elif head in ("MATCH", "NOMATCH"):
                checks.append((head, None, rest))
            elif head in ("FILEMATCH", "FILENOMATCH"):
                rel, _, pattern = rest.partition(" ")
                checks.append((head, rel, pattern))
            else:
                raise ValueError("bad directive in %s: %r" % (path, line))
    return runs, checks


def run_case(name, case_dir, build_root, sdk, keep):
    build = os.path.join(build_root, name)
    if os.path.isdir(build):
        rmtree_retry(build)
    shutil.copytree(case_dir, build)
    shutil.copy(EDITOR, os.path.join(build, "game", "wysiwyg_editor.rpy"))

    runs, checks = read_expectations(os.path.join(build, "expect.txt"))
    exe = os.path.join(sdk, "renpy.exe")
    result_path = os.path.join(build, "game", "selftest_result.txt")

    for i in range(runs):
        if os.path.exists(result_path):
            os.remove(result_path)
        proc = subprocess.run(
            [exe, build],
            capture_output=True,
            timeout=240,
        )
        if not os.path.exists(result_path):
            tb = os.path.join(build, "traceback.txt")
            detail = ""
            if os.path.exists(tb):
                with io.open(tb, "r", encoding="utf-8", errors="replace") as handle:
                    detail = handle.read()[-1500:]
            return ["run %d/%d produced no selftest_result.txt (exit %s)\n%s"
                    % (i + 1, runs, proc.returncode, detail)]

    with io.open(result_path, "r", encoding="utf-8") as handle:
        result = handle.read()

    failures = []
    for kind, rel, pattern in checks:
        if rel is None:
            haystack = result
            label = "selftest_result.txt"
        else:
            target = os.path.join(build, "game", rel.replace("/", os.sep))
            if not os.path.exists(target):
                failures.append("%s: file %s missing" % (kind, rel))
                continue
            with io.open(target, "r", encoding="utf-8", errors="replace") as handle:
                haystack = handle.read()
            label = rel
        found = re.search(pattern, haystack, re.M)
        if kind in ("MATCH", "FILEMATCH"):
            if not found:
                failures.append("expected in %s: /%s/" % (label, pattern))
        else:
            if found:
                failures.append("forbidden in %s: /%s/ (matched %r)"
                                % (label, pattern, found.group(0)))

    if not failures and not keep:
        try:
            rmtree_retry(build, attempts=2)
        except OSError:
            pass
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", default=os.environ.get("RENPY_SDK", DEFAULT_SDK))
    parser.add_argument("--case", default=None, help="run only this case")
    parser.add_argument("--keep", action="store_true", help="keep build dirs")
    args = parser.parse_args()

    cases_dir = os.path.join(HERE, "cases")
    # outside the repo: Dropbox sync would lock files under tests/
    build_root = os.path.join(tempfile.gettempdir(), "wysiwyg-editor-tests")
    names = sorted(n for n in os.listdir(cases_dir)
                   if os.path.isdir(os.path.join(cases_dir, n)))
    if args.case:
        names = [n for n in names if args.case in n]
    if not names:
        print("no cases matched")
        return 2

    failed = 0
    for name in names:
        failures = run_case(name, os.path.join(cases_dir, name),
                            build_root, args.sdk, args.keep)
        if failures:
            failed += 1
            print("FAIL  %s" % name)
            for f in failures:
                print("      - %s" % f)
        else:
            print("PASS  %s" % name)

    print("\n%d/%d cases passed" % (len(names) - failed, len(names)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
