# Guia do projeto pdf-para-simulado (para quem for continuar o desenvolvimento)

> Este arquivo é lido automaticamente pelo Claude Code ao abrir a pasta. Ele descreve **como o
> projeto funciona por dentro**, as decisões já tomadas e os cuidados. Para o uso pelo
> professor, veja o [README.md](README.md) e a pasta [entrega/](entrega/).
>
> **Notas privadas** (link do app publicado, editor do Apps Script, sessão do Chrome, provas de
> teste): `dev_local/NOTAS-PRIVADAS.md`. A pasta `dev_local/` fica só neste computador (está
> no `.gitignore`); leia esse arquivo antes de testar ou publicar.

---

## 1. Quem usa e o que importa

- **Um único professor**, sem conhecimento técnico. Nada de multiusuário, listas de alunos,
  horários ou login de aluno: o dono do projeto já recusou essas ideias. Simplicidade vem antes
  de recursos.
- **O dono do projeto fala português e é iniciante em programação.** Explique em português,
  passo a passo, sem jargão; diga onde clicar.
- **Publicar sem perguntar (pedido do dono, 2026-11-04):** depois de terminar e testar uma
  mudança no código, atualize o editor do Apps Script e publique a *Nova versão* da implantação
  ativa (Implantar → Gerenciar implantações → lápis → *Nova versão* → Implantar). Avise que publicou.
- **Sempre perguntar antes de:**
  - `git commit` / `git push`;
  - qualquer coisa que saia do computador (Drive, e-mail, etc.).
- **Provas e gabaritos em PDF são de terceiros:** nunca vão para o GitHub nem para lugar
  nenhum. O `.gitignore` já bloqueia `*.pdf`, `saida/`, `*.zip`, `simulado_extraido/` e
  `dev_local/`.
- Nunca colocar o e-mail do usuário em código, commits de terceiros, requisições, etc.

---

## 2. Visão geral

```
PDF da prova ──► (navegador do professor) Pyodide + PyMuPDF roda extrair_questoes.py
                 └─► 1 imagem JPG por questão + nº de alternativas + matérias + avisos
             ──► Code.gs (Apps Script) salva imagens no Drive e cria a aba de respostas
Gabarito (clique, foto, PDF, Excel, texto) ──► interpretarGabarito() no Professor.html
Aluno abre ?prova=ID ──► Aluno.html mostra imagens, envia respostas ──► Code.gs corrige
```

| Pasta / arquivo | O que é |
|---|---|
| `apps_script/Code.gs` | Servidor (Apps Script V8): senha, planilha, Drive, correção, e-mail |
| `apps_script/Professor.html` | Área do Professor (`.../exec?professor`): envio do PDF, gabarito, publicar, resultados |
| `apps_script/Aluno.html` | Página do aluno (`.../exec?prova=ID` ou `.../exec` = lista dos abertos) |
| `apps_script/Extrator.html` | **GERADO**: o `extrair_questoes.py` embutido. Não edite à mão |
| `apps_script/appsscript.json` | Fuso `America/Sao_Paulo`, serviço avançado Drive v3, app executa como o dono e aceita acesso anônimo |
| `apps_script/LEIA-ME.md` | Instalação passo a passo (para quem instala) |
| `src/pdf_para_simulado/extrair_questoes.py` | O extrator (Python 3.14, PyMuPDF) |
| `src/pdf_para_simulado/gerar_apps_script.py` | Embute o `.py` no `Extrator.html` |
| `src/pdf_para_simulado/empacotar.py` | Monta o ZIP para o professor (troca `{{LINK_DO_PROFESSOR}}` só dentro do ZIP) |
| `entrega/` | 3 textos simples para o professor (ler primeiro, instalar, usar) |
| `site/` | **Site dos alunos** (Vercel): `index.html` GERADO do `Aluno.html` por `gerar_site.py` (não edite à mão); `gerar_config.js` + `vercel.json` montam `public/` com o `config.js` (variável `APP_URL` da Vercel) |
| `src/pdf_para_simulado/gerar_site.py` | Gera `site/index.html` a partir do `Aluno.html` (rode sempre que mudar o `Aluno.html`) |
| `PLANO_SUPABASE.md` | **Plano do site próprio** (Supabase + Cloudflare Pages), em construção por etapas. Leia antes de mexer em `web/` ou `supabase/` |
| `supabase/esquema.sql` | Banco do site próprio: tabelas, RLS, correção e código da prova DENTRO do banco, funções do aluno (`provas_abertas`, `prova_por_codigo`, `dados_prova`, `enviar_respostas`) e do professor. Rode no SQL Editor (pode rodar de novo) |
| `web/` | Site próprio: `index.html` (aluno/professor), `aluno.html` (código ou link; fala com o Supabase por `rpc()`; imagens da pasta pública `questoes`), `professor.html` (Área do Professor: veio do `apps_script/Professor.html`, com a camada `API`/`api()` falando com o Supabase nos mesmos formatos do Code.gs; login e-mail+senha, 1ª conta vira administrador, imagens viram WebP ~55 KB, gabarito por arquivo lido no navegador: SheetJS/PyMuPDF/Tesseract.js, código + prazo, Excel), `extrator.js` (GERADO por `gerar_apps_script.py`). `gerar_config.js` monta `public/` com `SUPABASE_URL` + `SUPABASE_KEY` (chave PÚBLICA; recusa a secreta) |

O pacote Python se chama `pdf_para_simulado` (antes era `escola`); o projeto no `pyproject.toml`
se chama `pdf-para-simulado`, igual ao repositório `github.com/rckhz/pdf-para-simulado`.

### Comandos

```bash
uv sync                                                    # prepara o ambiente (depois de clonar/mover a pasta)
uv run src/pdf_para_simulado/extrair_questoes.py prova.pdf # extrai pelo terminal -> saida/
uv run src/pdf_para_simulado/gerar_apps_script.py          # OBRIGATÓRIO após mudar o extrator (gera Extrator.html e web/extrator.js)
uv run src/pdf_para_simulado/gerar_site.py                 # OBRIGATÓRIO após mudar o Aluno.html
uv run src/pdf_para_simulado/empacotar.py "<link ?professor>"  # ZIP do professor
```

Não há suíte de testes no repositório; os testes ficam em `dev_local/testes/` (ver seção 8).

---

## 3. O extrator (`extrair_questoes.py`)

O texto no topo do arquivo (docstring "IDEIA GERAL") explica o básico. Resumo do que importa:

### 3.1 Três modos de achar questões (`montar_questoes`)
Tenta em ordem e fica com o primeiro que achar questões:
1. **`selo`**: número da questão numa fonte exclusiva (`FONTE_SELO = "SegoeUIBlack"`, ~6,4 pt;
   o cartão-resposta usa 6,0 pt e fica de fora). SARESP, Provão Paulista. 2 colunas.
2. **`rotulo`**: linha "Questão 01" / "QUESTÃO 01" (ou "Questão" + número em caixinha), "{02}"
   da Fuvest. Etec, Fatec, ENEM, Unesp, Fuvest.
3. **`numero`**: "6. Considere..." (número em negrito no começo; se achar pouco, aceita sem
   negrito: Unicamp, Cotuca). Filtra numeração que repete ou pula mais de 6.

### 3.2 Barreiras
Em cada página (`analisar_pagina`), "barreiras" iniciam algo novo: **selo** (questão),
**instrução** ("Leia o texto para responder às questões 05 e 06" → vira *Apoio* com `alvos`),
**cabeçalho** de seção ("LÍNGUA PORTUGUESA" → `assunto`). Tudo entre duas barreiras pertence à
de cima. Conteúdo antes da 1ª barreira de uma coluna é continuação do anterior.
- Uma linha que sozinha já é instrução completa com números citados vira bloco próprio
  (regra `sozinha` na divisão de blocos). Corrigiu Fuvest 2027 Q01–02 e Unesp Q12–15.
- "Leia o texto abaixo." (Prova Paraná) vale para as próximas questões até o próximo texto
  (`apoio_aberto`).
- ENEM: questões 01–05 repetidas na opção espanhol são descartadas (fica a de inglês).
- Redação, "folha de respostas", rascunho: `PADRAO_FIM` / `PADRAO_FOLHA_RESPOSTAS` encerram
  até aparecer uma questão nova.

### 3.3 Rodapé, cabeçalho e lixo
- `textos_de_rodape(doc)`: textos que se repetem em várias páginas perto da borda (chaves de
  texto, `("y", y)` e `("fig", …)` para figuras repetidas) são escondidos.
- Linha só com número no rodapé conta como **número da página** apenas se estiver perto do
  número real (`abs(n - (pagina+1)) <= 3`). Antes, um "90" de fração do ENEM sumia
  (Q137/178/180).
- Figuras pequenas repetidas (`< 15×15`) só vão para `escondidos`; imagem muito fina
  (`altura < 8` e `largura > 100`, separador da Fuvest 2027) é ignorada.

### 3.4 Alternativas
`num_alternativas` (4 = A–D, 5 = A–E) vem do texto ("(A)", "A)", "a)") ou, em questão que é
imagem, da contagem de linhas pelos pixels (`achar_corte_alternativas`). Se não der, usa o mais
comum na prova e avisa. O aluno só vê as letras que existem.

### 3.5 Numeração que não começa no 1 (`primeira`)
ENEM 2º dia vai de 91 a 180. Se os números são sequenciais a partir de N>1,
`primeira = N`. **Internamente as questões são sempre 1..total** (arquivos `q01.jpg`…); só a
exibição e o casamento com o gabarito usam o deslocamento. `primeira` vai no resultado e é
salvo na planilha (coluna `Primeira`).

### 3.6 Título (`titulo_da_prova`)
Pega o título da capa, com regras: ENEM ("1º dia" + "ENEM 2025" na capa →
`ENEM 2025 · Caderno 7 Azul`), versão da prova (linha "V1" / "Versão 1" / "Tipo A" →
` · V1`). O título serve para o nome sugerido e para sugerir o gabarito certo.

### 3.7 Resultado de `extrair_em_etapas` (gerador)
Emite `("progresso", feitas, total, msg)` e no fim `("fim", dict)` com:
`ok, titulo, dia, paginas, questoes[] (numero, selo, arquivo, assunto, jpg, largura, altura,
paginas, apoios, unida, alternativas, num_alternativas, avisos), gabarito, status_gabarito,
avisos_gerais, num_apoios, primeira, relatorio`.

### 3.8 Regras de ouro ao mexer no extrator
1. Depois de mudar o `.py`, rode `gerar_apps_script.py` (senão o app continua com o antigo).
2. **Faça teste de regressão de imagens** com todas as provas de `dev_local/provas/`
   (`dev_local/testes/comparar_img.py <py_antigo> pdfs…`): qualquer imagem diferente precisa
   ser uma melhoria intencional. Mudanças "pequenas" costumam quebrar outra prova.
3. Cuidado com `\uXXXX` e caracteres invisíveis ao editar (já aconteceu de virar caractere
   real e quebrar regex).
4. **Leia o texto da página sempre com `texto_dict(pagina)`**, nunca `pagina.get_text("dict")`
   direto: essa leitura é a parte mais lenta e ficava repetida 4 a 9 vezes por página
   (rodapé, cabeçalho, línguas, cada modo). `texto_dict` guarda a leitura de cada página do
   documento atual. Meça o tempo ao acrescentar passadas pela prova inteira (no navegador,
   pelo Pyodide, tudo fica de 3 a 5 vezes mais lento).

---

## 4. O servidor (`Code.gs`)

### 4.1 Armazenamento (criado sozinho na 1ª vez)
- **Script Properties:** `PLANILHA_ID`, `PASTA_ID`, `SAL`, `SENHA_HASH` (SHA-256 com sal),
  `EMAIL_NOTIFICACAO`, `MOSTRAR_CERTA` (`'sim'`/`'nao'`).
- **Planilha "Simulados - Resultados":**
  - aba **`Provas`**, colunas `COLUNAS_PROVAS` (ordem importa, `lerProvas_` lê por índice):
    `ID, Nome, Questões, Situação, Criado em, Pasta, Aba de respostas, Gabarito (JSON),
    Matérias (JSON), Imagens (JSON com IDs do Drive), Avisos (JSON), Alternativas (JSON),
    Primeira`. Coluna nova vai **sempre no fim**, e `criarProva` corrige o título em
    planilhas antigas (laço `['Alternativas','Primeira']`).
  - uma aba **`Respostas · <nome>`** por simulado: `Data/hora, Nome completo, Turma, Acertos,
    Nota (0-10), Situação, Q01..Qn` (cabeçalho usa a numeração real: `Q91..Q180`). Células
    pintadas de verde/vermelho/cinza (`cores_`).
- **Pasta "Simulados - Imagens das Questões"** no Drive, com uma subpasta por simulado
  (imagens + `relatorio.txt`).

### 4.2 Sessão do professor
Senha → `entrarProfessor` devolve um token (UUID) guardado no `CacheService` por 6 h
(`DURACAO_SESSAO_SEG`). Toda função do professor chama `exigirProfessor_(token)`; se expirou,
lança `SESSAO_EXPIRADA` e a página pede a senha de novo.

### 4.3 Fluxo de um simulado
`criarProva` (Rascunho) → `salvarImagem` ×N (em paralelo, pelo navegador) → `concluirImagens`
→ `salvarGabarito` → `mudarSituacao` (Publicado / Encerrado) → alunos: `dadosSimulado`,
`imagemQuestao`, `enviarRespostas` → professor: `resultados`.

### 4.4 Gabarito, correção e questões anuladas
- Gabarito = array de letras `A`–`E`, `''` (falta) ou **`'X'` = `ANULADA`**.
  `valeNoGabarito_(l)` aceita letras e `X`. `gabaritoCompleto_` exige todas preenchidas.
- `corrigir_`: `validas` = questões não anuladas; `nota = acertos / validas × 10` (1 casa).
  Ex.: 90 questões com 3 anuladas → vale 87. Resposta em branco conta como errada.
- `salvarGabarito` chama `recorrigir_`: **recalcula as notas de quem já respondeu**.
- Sem gabarito completo, a resposta fica "Aguardando gabarito" e é corrigida depois.
- **Letra certa:** o gabarito nunca vai para a página do aluno ANTES do envio. Depois do envio,
  a correção vai como `certa/errada/branco/anulada` e, se `MOSTRAR_CERTA` não for `'nao'`, também
  a letra certa de cada questão. **O padrão é mostrar** (de propósito: os simulados são treino);
  o professor desliga em ⚙ Configurações. O aluno pode enviar mais de uma vez (sem trava).
  O dono já decidiu manter assim: não propor "esconder por padrão", trava de reenvio nem modo
  "valendo nota".
- **Toda função do `Code.gs` sem `_` no fim pode ser chamada por QUALQUER visitante** (console
  do navegador: `google.script.run.nomeDaFuncao()`). Função de professor: `exigirProfessor_(token)`.
  Função para o dono rodar no editor (`redefinirSenhaProfessor`, `autorizarPermissoes`):
  `soNoEditor_()` (o e-mail de quem chama tem que ser o do dono do arquivo do projeto no Drive;
  não use `Session.getEffectiveUser()`, que pede uma permissão que o app não tem). Antes disso,
  um aluno podia apagar a senha do professor pelo console.
- `comoTexto_` coloca apóstrofo antes de `= + - @` (evita fórmula na planilha).

---

## 5. Área do Professor (`Professor.html`)

- Carrega Pyodide 0.29.5 do jsDelivr (`PYODIDE_URL`) + `pymupdf`, roda o Python do
  `Extrator.html` e mostra o progresso. As imagens sobem em paralelo (`emParalelo`).
- **O Python roda num Web Worker** (`CODIGO_WORKER`, criado por Blob; `prepararWorker`,
  `recortarNoWorker`, `lerProva`): a página não congela (UERJ: maior trava 7,2 s → 0,1 s).
  Se o navegador não deixar criar o Worker, `lerProva` cai no modo antigo (`carregarPyodide` +
  `recortar`, na própria página). `PREPARO_PY` é o mesmo nos dois modos. Teste no Apps Script de
  verdade (link `/dev`): `teste_worker.js` mede o tempo e a maior trava da página.
- Se a prova não for aceita, a tela mostra `r.erro` do extrator (somatória, PDF escaneado...).
- Abas: **gabarito**, **publicar**, **resultados** (`abrirAba`).
- `numProva(n)` = número exibido (`n + primeira - 1`). Use sempre ele em textos para o professor.

### 5.1 Leitor de gabarito (`interpretarGabarito(texto, total)`)
É a parte mais delicada. Recebe texto (do OCR do Drive para foto/PDF, da conversão do Drive para
planilha, ou digitado) e devolve `{ conjuntos: [{ rotulo, respostas:{n:letra}, anuladas:[], dia,
serie }...] }`.
- Lê como **fluxo de tokens** com fila de números pendentes (número … letra). Número que não é
  consecutivo **limpa a fila** (senão o "2" de "2º dia" desloca todas as respostas).
- "Anulada/Anulado" vira token `#` e consome um número pendente para `anuladas`.
- Corta tudo depois de "gabarito de correspondência" (Fuvest).
- **Vários gabaritos na mesma folha** (6º/7º/8º ano, 1º/2º dia, Caderno 1/2, V1–V4): um título
  só abre um conjunto novo se a numeração **recomeçar**; senão só preenche `dia`/`serie` do
  atual. (OCR real do ENEM pôs "CADERNO 7" no meio da tabela e criava 2 gabaritos.)
- `gabaritosLadoALado`: tabelas com várias versões na mesma linha (Fuvest 2027, V1–V4).
- Tabela com código do item ("001 PS25-LP-024 B", Prova Paraná) também é entendida.
- `sugerirConjunto`: quando há vários conjuntos, sugere o que combina com o nome da prova
  (ano, série, dia, caderno, cor; +3 se o código da versão, ex. "V1", aparece como palavra).
- `usarConjunto`: aplica o deslocamento `primeira-1`, marca anuladas como `X` e mostra a
  mensagem "a prova passa a valer N".

### 5.2 Marcação manual
Cada questão tem botões A–E (só as letras que existem) e **"Anular questão"** (alterna `X`;
`pintarQuestao` mostra "Anulada (não conta na nota) · desfazer"). Salva sozinho
(`agendarSalvar`). O contador mostra "✅ Gabarito completo · 3 anuladas (a prova vale 87)".
"Questões que a turma mais errou" ignora anuladas.

---

## 6. Página do aluno (`Aluno.html`)

- **Dois lugares, um arquivo só:** dentro do Google (`.../exec?prova=ID`) e no **site dos alunos**
  (Vercel, `site/`). Motivo: em navegador com várias contas Google, o link `script.google.com` vira
  `.../u/1/...` e mostra "Não foi possível abrir o arquivo" (defeito do Google, sem conserto no
  Apps Script). Toda chamada ao servidor passa por `servidor(funcao, ...args)`: no Google usa
  `google.script.run`; no site faz `fetch` POST (texto JSON, `credentials: 'omit'`, sem contas) no
  `doPost` do `Code.gs`, que só aceita `simuladosAbertos`, `dadosSimulado`, `imagemQuestao`,
  `enviarRespostas`. Professor configura o endereço do site em ⚙ Configurações (`SITE_ALUNOS`);
  aí `linkAluno_` passa a dar o link do site. A Área do Professor continua só no Google.
- Script Property nova: `SITE_ALUNOS` (endereço do site, sem barra no fim).

- Nome + turma, uma questão por vez (imagem), grade de resumo, botão de revisão antes de enviar.
- **Rascunho** em `localStorage` por `DIAS_RASCUNHO` dias: se fechar a aba, recupera as
  respostas.
- `rotuloQ(n)` mostra a numeração real (com `primeira`).
- Depois de enviar: nota, acertos/total (sem anuladas), lista de cada questão
  (✓ / ✗ / em branco / ⊘ anulada) e, se permitido, a letra certa.

---

## 7. Cuidados com o Apps Script

- Arquivos `.html` passam pelo **HtmlService template**: `<?` e `?>` no texto quebram a página
  (o `Extrator.html` é incluído com `include()`, que não interpreta template).
- Só existem `.gs` e `.html` no Apps Script; por isso o Python vai dentro do `Extrator.html`.
- **Publicar sem trocar o link:** Implantar → Gerenciar implantações → lápis → Versão:
  *Nova versão* → Implantar. Criar "Nova implantação" gera **outro link** (não fazer).
- O link `/dev` roda o código salvo (para testar); o `/exec` roda a última versão publicada.
- Depois de salvar no editor, recarregue (F5) a aba do app para pegar o código novo.

---

## 8. Testes (em `dev_local/`, fora do Git)

| Onde | O que |
|---|---|
| `dev_local/testes/teste_interpretar.js` | ~40 testes do leitor de gabarito (OCR real SARESP, Vestibulinho, Fatec, planilhas, Paraná, ENEM D2, ONC, OBF, UEL...) |
| `dev_local/testes/fuvest2027/teste_fuvest.js` | Gabarito Fuvest 2027 (V1–V4 lado a lado, 3 layouts) |
| `dev_local/testes/gerar_interpretar.js` | **Rode antes dos testes** se mudou o `Professor.html`: recria `interpretar.js` a partir dele |
| `dev_local/testes/comparar_img.py` | Regressão de imagens: compara extrator antigo × atual em vários PDFs |
| `dev_local/testes/checar.py` | Resumo da extração de um PDF (questões, alternativas, avisos) |
| `dev_local/testes/mock_gas.js`, `teste_backend.js` etc. | Simulação do Apps Script no Node para testar o `Code.gs` |
| `dev_local/cdp/` | Scripts Puppeteer para o editor do Apps Script e testes de ponta a ponta (ver notas privadas) |
| `dev_local/provas/` | 25 PDFs de provas/gabaritos usados nos testes (terceiros, nunca publicar) |

```bash
cd dev_local/testes
node gerar_interpretar.js && node teste_interpretar.js && node fuvest2027/teste_fuvest.js
```

---

## 9. Histórico das decisões (resumo)

- **v1 (commit c22924f):** simulados a partir de PDF, selos SARESP/Provão, gabarito por arquivo.
- **v2 (commit 533c8a6):** ENEM, Prova Paraná, Fatec/Etec/Unicamp/Cotuca/Unesp, revisão do aluno.
- **v3 (2026-11-03):**
  - Fuvest 2027 (recortes de Q01–02, separador fino, gabarito V1–V4 lado a lado, versão no título);
  - ENEM 2º dia (numeração 91–180 via `primeira`, gabarito com "Anulado", título com caderno/cor,
    "90" de fração não some mais, OCR real com "CADERNO 7" no meio);
  - **questões anuladas** (botão "Anular questão"; nota sobre as válidas; aluno vê ⊘);
  - pacote renomeado de `escola` para `pdf_para_simulado`.
- **v4 (2026-11-03): olimpíadas** (OBMEP, Canguru, ONC, OBA), todas no modo `numero`:
  - capa com instruções E questões (OBMEP): número de questão vale se a fonte for bem maior
    que a dos itens das instruções (`tamanho_capa`);
  - fundo colorido da folha inteira é ignorado; número da folha ao lado do cabeçalho some;
  - com linha divisória alta no meio, ela é a divisa das colunas (não o `vao_colunas`), e
    desenho que passa um pouco dela é cortado na coluna;
  - "título de matéria" falso: letra menor que o texto, alternativa "(C) RSS", texto com
    outro texto/figura na mesma altura, legenda em cima de imagem pequena (`titulo_falso`);
  - "Problemas de N pontos" (Canguru) é título que só separa (não vira matéria nem apoio);
  - número com letra do nível ("A5.", "EQ3."), "Questão 2) (1 ponto)" (OBA), linha
    "2) - Nota obtida" escondida; alternativa colada no pé da folha não é rodapé;
  - no `montar_pagina_lado_a_lado`, só vale divisória no meio ou que acompanhe a área (o eixo
    de um globo não é coluna);
  - títulos: "OBMEP 2025 · Nível 1 · 1ª fase", "ONC 2025 · Nível A · 1ª fase", "Canguru 2023 · Nível B";
  - gabarito: folha de soluções ("QUESTÃO 1 – ALTERNATIVA A") e prova com "( X )" na certa.
  - ONC 2016–2025: código "AF2."/"AH4.“..." (nível + matéria; numeração que recomeça por
    matéria é renumerada em sequência, campo `prefixo` da Barreira), "A12" sem ponto;
    questão que começa à direita de uma foto vale como se começasse na margem
    (`montar_pagina_lado_a_lado`); desenho do PDF feito de retângulos afastados é separado
    (`separar`); gabarito em tabela com código ("A1 A2 ... A15" + letras, `gabaritosPorCodigo`),
    um conjunto por nível, "NULA" = anulada, "AouB" = 1ª letra; `sugerirConjunto` dá +3 ao nível
    que está no nome da prova.
- **v5 (2026-11-04): Rio, Paraná e Etec antigas**
  - UERJ: "Questão" + número grande EMBAIXO (`rotulo_partido`); página de língua (topo da folha diz
    "Espanhol/Francês/Inglês", `linguas`): a 1ª língua vira apoio ("Texto em espanhol") das questões
    dela, as outras ficam de fora; cabeçalho que muda de texto mas fica no mesmo lugar (chave `("pos", x, y)`,
    início ou fim da linha, com vizinhos ±1); 1ª linha de parágrafo colada ao rótulo é da questão de baixo;
  - instrução "O texto a seguir é referência para as questões 10 a 18" (UFPR) e "AS QUESTÕES 09 A 22
    REFEREM-SE..." (UERJ); "questões de números 18 e 19" (Etec 2012, `PADRAO_QUESTOES`);
  - UFPR: "02 - texto" (número + hífen); "►B)" = resposta certa: vira o gabarito e a seta é pintada de branco;
  - lado a lado: título/instrução centralizados valem a largura toda; cabeçalho de tabela numa faixa
    ("PRÓTONS | ELÉTRONS", Etec 2024) não é título (`titulo_falso` também no modo `rotulo`);
  - leitor de gabarito: avisa "parece ser a prova" quando o arquivo tem alternativas com texto
    e nenhuma resposta (`pareceProva`).
- **v6 (2026-11-04): UERJ 2º EQ e velocidade**
  - língua da folha lida linha por linha (`linhas_topo`; o recorte colava "Francêsfi") ou de um
    título "ESPANHOL" (dicionário `lingua`, `outra_lingua()`): só a 1ª língua fica. A UERJ 2025
    2º EQ virava 70 questões fora de ordem a partir da 23; agora são 60;
  - `texto_dict`: cada página é lida uma vez só (UERJ 28,9 s → 12,7 s; Fatec 8,8 s → 5,9 s),
    imagens idênticas nas 46 provas de teste.
- **v7 (2026-11-04): UFRGS, UEL, OBF, OBQ (`dev_local/provas/novos3/`)**
  - modo `numero`: "Questão 1." com ponto (OBF/OBQ, junto do `m_oba`); número SOZINHO na linha em
    negrito com o enunciado logo embaixo e alinhado (UEL);
  - modo `numero` também usa a linha vertical alta do meio como divisa de colunas (UFRGS/OBQ),
    exceto se algum texto atravessa a linha (questão de largura inteira; cabeçalho no topo não conta);
  - instrução "As questões de 01 a 08 estão relacionadas ao texto abaixo" (`referencia`); o
    número de linhas de um bloco conta linhas VISUAIS (`linhas_visuais`: pedaços justificados);
  - cabeçalho curto idêntico no mesmo lugar em ≥60% das folhas ("OBQ 2025", "A") = cabeçalho;
  - `titulo_falso`: última linha de parágrafo ("…Tibagy" + "LTDA.") não é título de matéria;
  - pedaço de questão que é só um fio fino (<15 de altura) é descartado (`finalizar_trechos`);
  - `PADRAO_DISCURSIVAS` ("Parte II – Questões Analítico-Expositivas"): barreira `fim` com
    texto "discursivas: …"; as questões depois dela saem do simulado (com aviso);
  - erros claros: prova de SOMATÓRIA (UEM/UFSC: "04)" e "08)" sem "03)") e PDF escaneado;
  - gabarito: "Q1 (c); Q2 (a)" e um conjunto por "Nível Jr/I/II/III" (OBF, `niveis`).
  - Lista completa para regressão: `dev_local/testes/lista_provas.txt` (54 provas).
- Opção recusada para anuladas: "mostrar a nota com gabarito incompleto". Motivo: uma questão
  esquecida viraria "anulada" sem o professor perceber.

## 10. Ideias / pendências conhecidas

- **Revisão manual antes de enviar** (ver cada questão, excluir, juntar com a seguinte): é a
  ideia mais útil do plano de melhorias do dono. Tem que ser ANTES do envio, porque o PDF só
  existe no navegador durante a extração.
- O dono escreveu um plano de melhorias (ranking, histórico de tentativas, OCR, módulos). Ranking
  e histórico exigem identificar o aluno, o que contradiz "sem login de aluno": pergunte antes.
- PDFs escaneados (sem texto) não funcionam. O OCR do Drive devolve só texto, sem posição (não
  serve para recortar). Tesseract/PaddleOCR não rodam no Pyodide; o caminho viável é o
  Tesseract.js no navegador (devolve a posição de cada palavra).
- Toda prova nova costuma revelar um formato diferente: teste a prova, compare as imagens com
  todas as outras provas e só então publique.
