"""
Monta o ZIP para mandar ao professor: os 3 textos de entrega/ + os 5 arquivos de apps_script/.

Uso (na pasta do projeto):
    uv run src/pdf_para_simulado/empacotar.py "https://script.google.com/macros/s/.../exec?professor"

O link do professor é colocado no "1 - LEIA PRIMEIRO.txt" só dentro do ZIP; ele não
fica salvo nos arquivos do repositório (assim o link não vai parar no GitHub).
Sem o link, o texto pede para o professor usar o "Caso 2" (instalar na conta dele).
"""

import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ENTREGA = RAIZ / "entrega"
APPS = RAIZ / "apps_script"
ZIP = RAIZ / "Simulados - para o professor.zip"
PASTA = "Simulados - para o professor"

TEXTOS = ["1 - LEIA PRIMEIRO.txt", "2 - Como instalar.txt", "3 - Como usar.txt"]
SISTEMA = ["Code.gs", "Professor.html", "Aluno.html", "Extrator.html", "appsscript.json"]
MARCADOR = "{{LINK_DO_PROFESSOR}}"


def main():
    link = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if link and "?professor" not in link:
        link += "?professor"
    substituto = link or "(ainda não há link: use o CASO 2 abaixo)"

    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in TEXTOS:
            texto = (ENTREGA / nome).read_text(encoding="utf-8").replace(MARCADOR, substituto)
            # CRLF + BOM: quebras de linha e acentos certos no Bloco de Notas do Windows
            texto = texto.replace("\r\n", "\n").replace("\n", "\r\n")
            z.writestr(f"{PASTA}/{nome}", "﻿" + texto)
        for nome in SISTEMA:
            z.write(APPS / nome, f"{PASTA}/arquivos do sistema/{nome}")

    with zipfile.ZipFile(ZIP) as z:
        assert z.testzip() is None, "ZIP corrompido"
        for nome in SISTEMA:
            assert z.read(f"{PASTA}/arquivos do sistema/{nome}") == (APPS / nome).read_bytes()
    print(f"OK: {ZIP} ({ZIP.stat().st_size // 1024} KB)")
    print("Link do professor:", link or "NENHUM (o professor vai precisar instalar na conta dele)")


if __name__ == "__main__":
    main()
