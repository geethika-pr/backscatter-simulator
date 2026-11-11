"""
Safety check: every Param in core/registry.py must be an argument the
matching GNU Radio block's __init__ actually accepts, because the
middleware passes ALL registry params to the block (_build_node()). A name
the block doesn't know crashes the run on Start. Also flags block files
that don't parse, and registry defaults that differ from the block's own.
No GNU Radio needed. From the project root:
    python3 Test/check_registry_vs_blocks.py
Run it after regenerating any block in GRC or adding a registry Param.
"""
import ast
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORK = sys.argv[1] if len(sys.argv) > 1 else ROOT
BLOCKS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "blocks")
sys.path.insert(0, WORK)
import core.registry as registry
bad = 0
for tname, nt in registry.NODE_TYPES.items():
    path = os.path.join(BLOCKS, nt.class_module + ".py")
    if not os.path.exists(path):
        print(f"  (skip {tname}: {nt.class_module}.py not staged)"); continue
    src = open(path).read()
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        print(f"  !! {tname}: {nt.class_module}.py does not parse ({ex.msg}, line {ex.lineno}) -- would fail on import"); bad += 1; continue
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == nt.class_name), None)
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    args = {a.arg for a in init.args.args[1:]}
    defaults = dict(zip([a.arg for a in init.args.args[1:]][-len(init.args.defaults):], [ast.literal_eval(d) if not isinstance(d, ast.Name) else None for d in init.args.defaults]))
    reg = {p.name: p.default for p in nt.params}
    missing = set(reg) - args
    status = "OK " if not missing else "CRASH"
    if missing: bad += 1
    diffs = {k: (reg[k], defaults.get(k)) for k in reg if k in defaults and defaults[k] is not None and reg[k] != defaults[k]}
    print(f"{status} {tname:20s} registry->block unknown args: {sorted(missing) or '-'}" + (f"   default differs: {diffs}" if diffs else ""))
print("registry/block mismatches that would crash:", bad)
sys.exit(1 if bad else 0)
