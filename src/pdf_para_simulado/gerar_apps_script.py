"""
Gera o arquivo apps_script/Extrator.html a partir de extrair_questoes.py.

Por quê? O Apps Script só aceita arquivos .gs e .html. A Área do Professor
roda o extrator Python DENTRO do navegador (com o Pyodide), então o código
Python precisa ir junto, embutido num .html. Guardamos o código em base64
(só letras e números) para que nenhum símbolo do Python (<, >, aspas...)
atrapalhe o HTML.

Rode sempre que mudar o extrair_questoes.py:
    uv run src/pdf_para_simulado/gerar_apps_script.py
"""

import base64
from pathlib import Path

PASTA_PROJETO = Path(__file__).resolve().parents[2]
ORIGEM = Path(__file__).resolve().parent / "extrair_questoes.py"
DESTINO = PASTA_PROJETO / "apps_script" / "Extrator.html"


def main():
    codigo = ORIGEM.read_bytes()
    compile(codigo, str(ORIGEM), "exec")   # garante que o Python não tem erro de sintaxe
    b64 = base64.b64encode(codigo).decode("ascii")
    DESTINO.write_text(
        "<!-- GERADO AUTOMATICAMENTE por src/pdf_para_simulado/gerar_apps_script.py. Não edite à mão. -->\n"
        f"<script>const EXTRATOR_PY_B64 = \"{b64}\";</script>\n",
        encoding="utf-8",
    )
    print(f"OK: {DESTINO} ({len(codigo) // 1024} KB de Python)")


if __name__ == "__main__":
    main()
