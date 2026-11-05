"""
Gera o SITE DOS ALUNOS (pasta site/) a partir de apps_script/Aluno.html.

Por quê? Em navegador com várias contas Google logadas, o link script.google.com às vezes
abre ".../u/1/..." e mostra "Não foi possível abrir o arquivo". O site dos alunos fica fora
do Google (ex.: Vercel) e conversa com o Apps Script pela "porta de entrada" (doPost do
Code.gs), sem mandar as contas do navegador. A página é a MESMA do Apps Script (Aluno.html):
aqui só trocamos o que é do Apps Script pelo que o site precisa.

Rode sempre que mudar o Aluno.html:
    uv run src/pdf_para_simulado/gerar_site.py
"""

from pathlib import Path

PASTA_PROJETO = Path(__file__).resolve().parents[2]
ORIGEM = PASTA_PROJETO / "apps_script" / "Aluno.html"
DESTINO = PASTA_PROJETO / "site" / "index.html"

# No Apps Script, o servidor escreve o ID do simulado na página. No site, ele vem do link (?prova=...).
TEMPLATE_PROVA = "let provaId = <?= provaId ?>;"
SITE_PROVA = "let provaId = new URLSearchParams(location.search).get('prova') || '';"
# O endereço do Apps Script (APP_URL) vem do config.js, criado pela Vercel ao publicar o site.
CONFIG = ('<script src="config.js"></script>\n'
          '<script>if (!window.APP_URL) document.addEventListener("DOMContentLoaded", () => {\n'
          '  document.getElementById("progressText").textContent = '
          '"Site não configurado: falta o endereço do app (APP_URL) na Vercel."; });</script>\n')


def main():
    html = ORIGEM.read_text(encoding="utf-8")
    if html.count(TEMPLATE_PROVA) != 1:
        raise SystemExit(f"Não achei a linha {TEMPLATE_PROVA!r} no Aluno.html")
    html = html.replace(TEMPLATE_PROVA, SITE_PROVA)
    if "<?" in html or "?>" in html:
        raise SystemExit("O Aluno.html tem outro marcador do Apps Script (<? ?>): o site não saberia o que pôr.")
    i = html.index("<script>")
    html = html[:i] + CONFIG + html[i:]
    aviso = "<!-- GERADO AUTOMATICAMENTE por src/pdf_para_simulado/gerar_site.py a partir de apps_script/Aluno.html. Não edite à mão. -->\n"
    DESTINO.parent.mkdir(exist_ok=True)
    DESTINO.write_text(aviso + html, encoding="utf-8", newline="\n")
    print(f"OK: {DESTINO}")


if __name__ == "__main__":
    main()
