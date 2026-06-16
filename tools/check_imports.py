import ast, os, sys
ROOT = "."
PREFIXES = ("contracts", "agents", "mcp_connectors", "services", "rag", "libs", "db")

def resolve_module(mod):
    base = os.path.join(ROOT, *mod.split("."))
    if os.path.exists(base + ".py"):
        return ("module", base + ".py")
    if os.path.isdir(base):
        return ("package", base)
    return None

def defined_names(path):
    tree = ast.parse(open(path).read(), path)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name): names.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name): names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    return names

problems = []
for dirpath, _, files in os.walk(ROOT):
    if "__pycache__" in dirpath: continue
    for fn in files:
        if not fn.endswith(".py"): continue
        path = os.path.join(dirpath, fn)
        tree = ast.parse(open(path).read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(PREFIXES):
                res = resolve_module(node.module)
                if not res:
                    problems.append(f"{path}: cannot locate local module {node.module}")
                    continue
                kind, loc = res
                if kind == "module":
                    have = defined_names(loc)
                    for a in node.names:
                        if a.name == "*": continue
                        if a.name not in have:
                            problems.append(f"{path}: '{a.name}' not found in {node.module}")
                else:  # package: imported names may be submodules, subpackages, or __init__ names
                    initp = os.path.join(loc, "__init__.py")
                    init_names = defined_names(initp) if os.path.exists(initp) else set()
                    for a in node.names:
                        if a.name == "*": continue
                        if os.path.exists(os.path.join(loc, a.name + ".py")): continue
                        if os.path.isdir(os.path.join(loc, a.name)): continue
                        if a.name in init_names: continue
                        problems.append(f"{path}: '{a.name}' not found in package {node.module}")
if problems:
    print("INCONSISTENCIES FOUND:"); print("\n".join(problems)); sys.exit(1)
print("All cross-module imports resolve.")
