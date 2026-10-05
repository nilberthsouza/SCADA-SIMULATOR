# Simulador SCADA de Subestação

Simulador gratuito para treinar manobras em subestações (barra simples, principal + transferência, barra dupla a 3 e 4 chaves, disjuntor e meio), com intertravamentos punitivos, defeitos, alarmes, SOE e manutenção guiada.

**Download:** veja a página do projeto (GitHub Pages) ou `docs/SimuladorSCADA.exe`.

## Rodar do código-fonte
```
python -m venv .venv
.venv\Scripts\pip install PySide6 numpy pyinstaller
.venv\Scripts\python main.py
```

## Gerar o exe
```
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name SimuladorSCADA main.py
```
