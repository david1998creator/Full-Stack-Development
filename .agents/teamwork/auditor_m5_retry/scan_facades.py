import ast
import os

print("--- AST Facade & Stub Scanner ---")
empty_or_constant_funcs = []
for root, dirs, files in os.walk('src/persistent_crewai'):
    for f in files:
        if f.endswith('.py'):
            filepath = os.path.join(root, f)
            with open(filepath, 'r', encoding='utf-8') as src_file:
                try:
                    tree = ast.parse(src_file.read(), filename=filepath)
                except Exception as e:
                    print(f"Error parsing {filepath}: {e}")
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Check body length
                        # If body is just 'pass' or 'return <constant>' or 'raise NotImplementedError'
                        if len(node.body) == 1:
                            stmt = node.body[0]
                            if isinstance(stmt, ast.Pass):
                                empty_or_constant_funcs.append((filepath, node.name, "pass"))
                            elif isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                                empty_or_constant_funcs.append((filepath, node.name, f"return {stmt.value.value}"))
                            elif isinstance(stmt, ast.Raise):
                                empty_or_constant_funcs.append((filepath, node.name, "raise"))

print(f"Found {len(empty_or_constant_funcs)} single-statement functions:")
for fp, name, reason in empty_or_constant_funcs:
    print(f"  {fp} -> {name}: {reason}")
