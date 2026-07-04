"""Which editor UI actions does no test ever touch?

Extracts every wysiwyg_* callable referenced from screen actions
(Function(...) targets - the surface users can actually click) in
game/wysiwyg_editor.rpy and cross-references them against every script
under tests/cases/. Anything listed here is clickable in the editor but
never exercised by the suite - either cover it or know why it is trivial.

Usage: python tests/coverage_audit.py
"""
import io, os, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

with io.open(os.path.join(REPO, "game", "wysiwyg_editor.rpy"), encoding="utf-8") as f:
    src = f.read()

ui_refs = set(re.findall(r"Function\((wysiwyg_\w+)", src))

tests_src = ""
for path in glob.glob(os.path.join(HERE, "cases", "*", "game", "*.rpy")):
    with io.open(path, encoding="utf-8") as f:
        tests_src += f.read()

tested = set(re.findall(r"(wysiwyg_\w+)\(", tests_src))

untested = sorted(ui_refs - tested)
print("UI actions never called by any test (%d of %d):" % (len(untested), len(ui_refs)))
for name in untested:
    print("  " + name)
