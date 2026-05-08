import os

def check_indentation(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if i >= 637:
                stripped = line.lstrip()
                if stripped.startswith('else:'):
                    indent = len(line) - len(stripped)
                    print(f"Line {i}: 'else:' at indent {indent}")
                elif stripped.startswith('if '):
                    indent = len(line) - len(stripped)
                    print(f"Line {i}: 'if' at indent {indent}")
                elif stripped.startswith('for '):
                    indent = len(line) - len(stripped)
                    print(f"Line {i}: 'for' at indent {indent}")
                elif stripped.startswith('with '):
                    indent = len(line) - len(stripped)
                    print(f"Line {i}: 'with' at indent {indent}")

check_indentation('d:/AAPROYECTOS/LOL/app.py')
