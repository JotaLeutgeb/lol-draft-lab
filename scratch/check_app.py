with open('d:/AAPROYECTOS/LOL/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines, 1):
        if 800 <= i <= 920:
            print(f"{i:4} : {repr(line)}")
