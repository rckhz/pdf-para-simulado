# Simulados Online a partir de provas em PDF

Transforma o PDF de uma prova num **simulado online**, pensado para **um professor sem
conhecimento técnico**:

1. o professor envia o PDF e o sistema recorta cada questão sozinho (enunciado, textos de
   apoio, figuras e alternativas) numa imagem;
2. marca o gabarito, clicando nas letras ou enviando uma foto, PDF, planilha (Excel) ou texto;
3. libera o simulado e manda o link aos alunos (botão de WhatsApp);
4. os alunos respondem pelo celular ou computador e veem a nota na hora, com a lista do que
   acertaram e erraram e a resposta certa (o professor pode esconder a resposta certa);
5. o professor vê a média, as notas e as questões que a turma mais errou.

Tudo roda no **Google Apps Script** (gratuito, na conta Google do professor): as imagens ficam no
Google Drive e as respostas numa planilha do Google. O recorte do PDF é feito **no navegador do
professor**, com Python rodando via [Pyodide](https://pyodide.org) + [PyMuPDF](https://pymupdf.readthedocs.io).

## Provas compatíveis

Testado com estas provas (PDF original, com texto, não escaneado):

| Prova | Testada com | Questões | Observações |
|---|---|---|---|
| **ENEM** | 2024 e 2025, 1º e 2º dia | 90 | Redação no meio do caderno é pulada; questões 01–05 só na versão de inglês; o 2º dia mantém a numeração 91–180 (também no gabarito, que marca as questões anuladas) |
| **Fuvest** | 2025 e 2027, 1ª fase | 90 / 80 | Número entre chaves ("{02}"), 2 colunas; gabarito com as versões V1–V4 lado a lado (o app separa e sugere a versão da prova) |
| **Unicamp** | 2025, 1ª fase (prova Q) | 72 | Alternativas a) a d) |
| **Unesp / Vunesp** | Unesp 2025 1ª fase; Unifipa Medicina 2025; Vestibulinho Unesp Bauru | 50–90 | "QUESTÃO 01" com etiqueta colorida |
| **Cotuca** (Unicamp) | Processo Seletivo 2020 | 36 | Redação antes das questões; alternativas a) a e) |
| **Vestibular Fatec** | 2020, 2024, 2025 e 2026 | 54–64 | Modelos "Questão 01" e "6. Considere..." (1 ou 2 colunas) |
| **Vestibulinho Etec** | 2º sem. 2012 e 2024; 1º e 2º sem. 2025; 1º sem. 2026 | 50 | Questões lado a lado; "questões de números 18 e 19" |
| **UERJ** (Rio de Janeiro) | 2025, 1º Exame de Qualificação | 60 | "Questão" pequeno com o número grande embaixo; parte de língua (espanhol/francês/inglês) fica com a 1ª língua; gabarito com as línguas lado a lado |
| **UFPR** (Paraná) | 2026, 1ª fase (caderno com gabarito) | 90 | "02 - Na figura..."; resposta marcada com "►" no caderno vira o gabarito (a seta some da imagem) |
| **Provão Paulista** | 2 cadernos | 42–48 | Selos numerados em 2 colunas |
| **SARESP** | simulados | — | Selos numerados em 2 colunas |
| **Prova Paraná Mais** | 2025, 3ª série EM, caderno 1 | 77 | Gabarito com Caderno 1 e 2 na mesma folha |
| **OBMEP** | 2025, 1ª fase níveis 1 e 3; Mirim 1 | 15–20 | Questões 1–4 na capa, fundo colorido; gabarito lido da folha de soluções ("QUESTÃO 1 – ALTERNATIVA A") |
| **Canguru de Matemática** | 2023, níveis E, B e J | 24–30 | "Problemas de 3/4/5 pontos" separam as partes |
| **ONC** (Olimpíada Nacional de Ciências) | 1ª fase de 2016 a 2025 (vários níveis) | 15–20 | Número com o nível e a matéria ("A5.", "AF2.", "EQ3."); gabarito em tabela "A1 A2..." com os 4 níveis (o app sugere o nível; "NULA" vira anulada) |
| **OBA** (Astronomia) | 2025, níveis 3 e 4 | 10 | "Questão 2) (1 ponto)"; gabarito com "( X )" na certa (as que não têm X a professora marca) |

Gabaritos: o sistema lê foto, PDF, planilha (Excel) ou texto, inclusive folhas com vários
gabaritos (por ano, série, dia ou caderno) e tabelas com código do item ("001 PS25-LP-024 B").

Outras provas com um desses formatos também devem funcionar (veja "Modelos de prova
reconhecidos" abaixo). **Não funcionam:** PDFs escaneados (foto da prova, sem texto).

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
src/pdf_para_simulado/              CÓDIGO-FONTE do extrator (para desenvolvimento)
  extrair_questoes.py    acha as questões no PDF e gera as imagens
  gerar_apps_script.py   embute o extrair_questoes.py no apps_script/Extrator.html
  empacotar.py           monta o ZIP de entrega para o professor
entrega/                 textos simples para o professor (instalar e usar)
```

**Por que o Python fica dentro de um `.html`?** O Google Apps Script só aceita arquivos
`.gs` e `.html`. Por isso o `extrair_questoes.py` vai embutido no `Extrator.html` e roda no
navegador do professor (via Pyodide). Para mudar o extrator, edite o `.py` e rode
`uv run src/pdf_para_simulado/gerar_apps_script.py`.

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

Modelos de prova reconhecidos:
- **com selo numerado** (01, 02, 03...) em **duas colunas**: SARESP, Provão Paulista;
- **com a linha "Questão 01"** (ou "Questão" + número numa caixinha): Vestibulinho Etec e
  Vestibular Fatec. Em geral é uma coluna, mas páginas com questões **lado a lado** também são
  entendidas, em uma ou duas colunas (ex.: Provão Paulista 2023 com "QUESTÃO 01");
- **com o número em negrito no começo da questão** ("6. Considere..."), em uma ou duas
  colunas: Vestibular Fatec 2025. O texto logo abaixo do título da matéria ("LÍNGUA
  PORTUGUESA") vira texto de apoio das questões daquela matéria;
- **ENEM** ("QUESTÃO 01" em uma ou duas colunas): "Texto para as Questões de 06 a 10" vira texto
  de apoio; a redação no meio do caderno é pulada e as questões continuam depois dela. As
  questões 01 a 05 aparecem duas vezes (inglês e espanhol): fica só a primeira versão (inglês).
- **Fuvest** ("{02}" sozinho na linha, "#####" no fim da questão), em 2 colunas;
- **Unicamp, Cotuca** e outras com **alternativas em minúscula** ("a)", "b)"...) e número sem
  negrito ("7. As alternativas..."); a redação no começo do caderno é pulada;
- **Prova Paraná Mais** ("001) (PS25-LP-024) ..."): "Leia o texto abaixo." vale para todas as
  questões seguintes até o próximo texto; a caixa "ATENÇÃO! Agora, você vai responder a questões
  de Matemática." dá a matéria. O gabarito em tabela (nº, código do item, letra) é lido com os
  cadernos separados ("Caderno 1", "Caderno 2").

A capa, a contracapa, o cabeçalho e o rodapé repetidos, as marcas de impressão na borda, a coluna de rascunho, a folha de respostas e a
redação do fim são ignorados.

PDFs escaneados (foto, sem texto) não são suportados.

## Uso pelo terminal (opcional)

Precisa de [uv](https://docs.astral.sh/uv/) e Python 3.14.

```bash
uv run src/pdf_para_simulado/extrair_questoes.py prova.pdf   # gera saida/assets/q01.jpg..., relatorio.txt e ANSWER_KEY.js
uv run src/pdf_para_simulado/gerar_apps_script.py            # depois de mudar o extrator: atualiza apps_script/Extrator.html
uv run src/pdf_para_simulado/empacotar.py "<link do app>"    # gera o ZIP para o professor
```

## Instalação do app

Veja [apps_script/LEIA-ME.md](apps_script/LEIA-ME.md) (passo a passo de uns 10 minutos) ou o texto
simplificado em [entrega/2 - Como instalar.txt](entrega/2%20-%20Como%20instalar.txt).

Para atualizar um app já publicado, use **Implantar → Gerenciar implantações → editar → Nova versão**
(assim o link continua o mesmo).

## Observações

- As provas em PDF **não** fazem parte do repositório (conteúdo de terceiros).
- O gabarito nunca é enviado para a página do aluno; a Área do Professor é protegida por senha.
