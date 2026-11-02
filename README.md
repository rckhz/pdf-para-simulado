# Simulados Online a partir de provas em PDF

Transforma o PDF de uma prova num **simulado online**, pensado para **um professor sem
conhecimento técnico**:

1. o professor envia o PDF e o sistema recorta cada questão sozinho (enunciado, textos de
   apoio, figuras e alternativas) numa imagem;
2. marca o gabarito, clicando nas letras ou enviando uma foto, PDF, planilha (Excel) ou texto;
3. libera o simulado e manda o link aos alunos (botão de WhatsApp);
4. os alunos respondem pelo celular ou computador e veem a nota na hora;
5. o professor vê a média, as notas e as questões que a turma mais errou.

Tudo roda no **Google Apps Script** (gratuito, na conta Google do professor): as imagens ficam no
Google Drive e as respostas numa planilha do Google. O recorte do PDF é feito **no navegador do
professor**, com Python rodando via [Pyodide](https://pyodide.org) + [PyMuPDF](https://pymupdf.readthedocs.io).

## Estrutura

> **Para usar o app, só a pasta `apps_script/` é necessária**: ela é o que se cola no
> Google Apps Script. A pasta `src/` é o **código-fonte do extrator** e só é usada por quem
> quiser modificá-lo.

```
apps_script/             O APP (é isto que vai para o Google Apps Script)
  Code.gs                servidor: senha, planilha, Drive, correção
  Professor.html         Área do Professor
  Aluno.html             página do aluno
  Extrator.html          o extrator em Python, embutido (GERADO: não edite à mão)
  appsscript.json        configurações do projeto
  LEIA-ME.md             passo a passo da instalação
src/escola/              CÓDIGO-FONTE do extrator (para desenvolvimento)
  extrair_questoes.py    acha as questões no PDF e gera as imagens
  gerar_apps_script.py   embute o extrair_questoes.py no apps_script/Extrator.html
  empacotar.py           monta o ZIP de entrega para o professor
entrega/                 textos simples para o professor (instalar e usar)
```

**Por que o Python fica dentro de um `.html`?** O Google Apps Script só aceita arquivos
`.gs` e `.html`. Por isso o `extrair_questoes.py` vai embutido no `Extrator.html` e roda no
navegador do professor (via Pyodide). Para mudar o extrator, edite o `.py` e rode
`uv run src/escola/gerar_apps_script.py`.

## Como o extrator funciona

- **Questões:** cada questão começa num "selo" com o número, escrito numa fonte exclusiva
  (`SegoeUIBlack`, ~6,4 pt). Procurar pela fonte evita confundir com anos, porcentagens e com
  os números do cartão-resposta.
- **Duas colunas:** lê a esquerda de cima para baixo, depois a direita. Questões que continuam
  em outra coluna ou página são unidas numa imagem só.
- **Textos de apoio:** "Leia o texto para responder às questões 05 e 06" vira um bloco que é
  repetido na imagem de cada questão citada.
- **Alternativas:** conta as linhas "(A)", "(B)"... pelos pixels, para saber se a questão vai
  até D ou até E (o aluno só vê as letras que existem).
- **Gabarito por arquivo:** fotos e PDFs são lidos pelo OCR do Google Drive; planilhas são
  convertidas pelo Drive. Folhas com vários gabaritos (6º/7º/8º ano, 1º/2º dia) viram uma
  pergunta ao professor, com sugestão do gabarito certo.

Funciona com provas no modelo em que cada questão tem o número num selo (01, 02, 03...).
PDFs escaneados não são suportados.

## Uso pelo terminal (opcional)

Precisa de [uv](https://docs.astral.sh/uv/) e Python 3.14.

```bash
uv run src/escola/extrair_questoes.py prova.pdf   # gera saida/assets/q01.jpg..., relatorio.txt e ANSWER_KEY.js
uv run src/escola/gerar_apps_script.py            # depois de mudar o extrator: atualiza apps_script/Extrator.html
uv run src/escola/empacotar.py "<link do app>"    # gera o ZIP para o professor
```

## Instalação do app

Veja [apps_script/LEIA-ME.md](apps_script/LEIA-ME.md) (passo a passo de uns 10 minutos) ou o texto
simplificado em [entrega/2 - Como instalar.txt](entrega/2%20-%20Como%20instalar.txt).

Para atualizar um app já publicado, use **Implantar → Gerenciar implantações → editar → Nova versão**
(assim o link continua o mesmo).

## Observações

- As provas em PDF **não** fazem parte do repositório (conteúdo de terceiros).
- O gabarito nunca é enviado para a página do aluno; a Área do Professor é protegida por senha.
