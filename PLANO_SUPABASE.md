# Plano: migrar o pdf-para-simulado para site próprio com Supabase

> Documento para orientar o Claude Code. Ele começa sem memória da conversa, então aqui está
> **tudo**: o que existe hoje, as decisões já fechadas (não reabrir), a arquitetura, os problemas
> com as soluções e as etapas. Escrito em 2026-11-06.

---

## 0. Como trabalhar comigo (regras para o Claude Code)

- A dona do projeto **fala português e é iniciante em programação**. Explique em português, passo a
  passo, sem jargão, dizendo onde clicar. Construa **em partes pequenas** e confirme antes de seguir.
- **Perguntar antes de:** `git commit`/`git push`; qualquer coisa que saia da máquina (subir para o
  Supabase, Cloudflare, e-mail); e antes de apagar/renomear arquivos existentes.
- **Nunca** colocar no código, em commits ou em requisições: a chave de administrador (service role)
  do Supabase, senhas, ou o e-mail pessoal da dona.
- **O app atual no Google Apps Script continua funcionando** e NÃO deve ser quebrado. Só será
  desligado depois que o site novo for testado com uma turma de verdade.
- Provas e gabaritos em PDF são de terceiros: nunca vão para o GitHub (o `.gitignore` já bloqueia).

---

## 1. O que existe hoje

Repositório: `github.com/rckhz/pdf-para-simulado`.

- **App atual (Google Apps Script):** `apps_script/Code.gs` (servidor), `Professor.html`,
  `Aluno.html`, `Extrator.html` (gerado). Guarda imagens no Google Drive e resultados numa planilha.
  Funciona, mas depende de conta Google e é de um professor só.
- **Extrator de questões (reutilizável):** `src/pdf_para_simulado/extrair_questoes.py` (PyMuPDF).
  Roda **no navegador do professor** via Pyodide (embutido no `Extrator.html`). Recebe o PDF e gera
  uma imagem por questão + nº de alternativas + matérias + avisos + gabarito (quando está no PDF).
  Já lida com SARESP, ENEM, Fuvest, Unicamp, Unesp, Etec, Fatec, UERJ, OBMEP e outras.
- Detalhes internos do extrator estão no `CLAUDE.md` do repositório — **leia antes de mexer nele**.

**Importante:** o extrator (a parte mais difícil e já pronta) deve ser **reaproveitado quase como
está**. A migração é do "redor" (onde guardar e como as telas conversam com o servidor), não do
miolo que recorta as questões.

---

## 2. Objetivo e por que Supabase

Sair do Google e virar um site próprio, para: (a) acabar com o bug das múltiplas contas Google;
(b) permitir **vários professores** numa escola, gerenciados por um administrador; (c) não depender
de conta Google de ninguém.

Decidiu-se pelo **Supabase** (banco + login + arquivos num lugar só, grátis) em vez de manter o
Google. Na escala real (~30 questões, ~100 alunos por simulado), o limite de tráfego do Supabase é
folgado (ver seção 6, P1).

---

## 3. Decisões já fechadas (NÃO reabrir)

1. **Plataforma:** Supabase (Postgres + Auth + Storage + Edge Functions) para o backend;
   **Cloudflare Pages** para hospedar o site (grátis e permite uso por escola; a Vercel grátis é só
   uso pessoal/não comercial, por isso foi descartada para produção).
2. **Uma instalação por escola** (cada escola: seu projeto Supabase + seu site). Dados separados.
3. **Papéis:** Administrador, Professor, Aluno (ver seção 4).
4. **Professores são cadastrados pelo administrador** (sem cadastro livre). Login é **do app**
   (e-mail + senha no Supabase Auth), **não** é login Google.
5. **Aluno entra sem login**, por link direto **ou** digitando um **código** da prova. A lista
   mostra **só as provas abertas** naquele momento.
6. **Código por prova, com janela de tempo:** ao "Abrir" a prova, gera-se um código (ex.: `K7P2QX`,
   6 caracteres). Ele vale até o professor "Encerrar" **ou** até um fecho automático de segurança
   (ex.: expira sozinho em algumas horas). Cada abertura gera um código novo (o antigo morre).
7. **Correção e gabarito no servidor:** o gabarito fica numa tabela que o aluno **não consegue ler**;
   a correção acontece dentro do Supabase; o navegador do aluno manda as respostas e recebe só a
   nota e a correção.
8. **Comportamentos herdados do app atual, mantidos:** a letra certa **aparece** ao aluno depois do
   envio (padrão ligado; dá para o professor desligar); o aluno **pode enviar mais de uma vez**.
9. **Resultados no Supabase** (não em planilha Google). O professor vê uma **tela de resultados ao
   vivo** + botão **"Baixar Excel"** (o Excel é uma cópia do momento, um extra, não o que atualiza).
10. **Imagens das questões em WebP, tamanho ajustado para celular**, para economizar tráfego (P1).
11. **Conta do Supabase deve ser institucional** (e-mail da escola/projeto), nunca pessoal (LGPD).
12. **Prioridade: custo zero.** Os simulados do app atual **não serão migrados** (cada escola cria
    os seus).

---

## 4. Papéis e acesso

| Perfil | O que faz | Como entra |
|---|---|---|
| **Administrador** | Cadastra professores (e-mail + senha provisória), redefine senha, vê tudo da escola | Login (e-mail + senha). É a **1ª conta** criada |
| **Professor** | Cria/apaga simulados, sobe o PDF, marca gabarito, abre/encerra, vê resultados, baixa Excel. Vê **só os simulados dele** | Login (troca a senha provisória no 1º acesso) |
| **Aluno** | Responde, vê nota e correção | **Sem login**: link da prova ou código |

**Regras de acesso ao banco (Row Level Security — RLS):**
- Aluno anônimo: só **lê** provas com status "aberta" e só **grava** envios. Nunca lê a tabela de
  gabarito nem a de professores.
- Professor: só enxerga e mexe nos simulados com o `professor_id` dele.
- Administrador: enxerga tudo da escola.
- A **chave pública (anon)** do Supabase vai no site (é feita para isso, segura com RLS). A **chave
  de administrador (service role)** NUNCA vai no site nem no GitHub — fica só dentro de uma Edge
  Function (ver seção 5 e 7).

---

## 5. Arquitetura

```
Navegador do PROFESSOR
  ├─ extrator (Pyodide + PyMuPDF, reaproveitado) recorta o PDF em imagens
  ├─ converte cada imagem para WebP (tamanho de celular) — ver nota abaixo
  └─ sobe as imagens para o Storage e cria o simulado via supabase-js

Navegador do ALUNO
  └─ abre por link/código → baixa as imagens do Storage → envia respostas

SUPABASE
  ├─ Auth: administrador e professores (e-mail + senha)
  ├─ Banco (Postgres) com RLS: escola/config, professores, simulados, questões,
  │   gabaritos (TRANCADO), envios
  ├─ Storage: bucket com as imagens das questões (WebP)
  ├─ RPC/Function de correção: recebe respostas, lê o gabarito (que o cliente não vê),
  │   calcula a nota, grava o envio e devolve só nota + correção
  └─ Edge Function "criar professor": usa a chave de administrador (guardada no Supabase),
      só aceita pedido do admin logado

SITE hospedado no Cloudflare Pages (arquivos estáticos) — mesmo código para todas as escolas
```

**Nota sobre WebP:** a forma mais simples e confiável é o PyMuPDF renderizar a imagem e o
**navegador** converter para WebP (via `canvas.toBlob('image/webp', ...)`) antes de subir, evitando
depender de Pillow dentro do Pyodide. O Claude Code decide, mas essa é a rota recomendada.

**Reaproveitamento:** a lógica de recorte do `extrair_questoes.py` NÃO muda (fidelidade ao PDF é
obrigatória — ver `CLAUDE.md`). Mudam só: o formato de saída (WebP) e para onde as imagens vão
(Storage, não Drive). As telas do aluno e do professor podem reaproveitar muito do HTML atual,
trocando as chamadas ao Apps Script por chamadas ao `supabase-js`.

---

## 6. Modelo de dados (ponto de partida — o Claude Code pode ajustar)

- **config_escola:** nome da escola, preferências (ex.: mostrar letra certa ao aluno sim/não).
- **professores:** ligada ao usuário do Auth; `id`, `nome`, `email`, `papel` (admin/professor).
- **simulados:** `id`, `professor_id`, `titulo`, `total_questoes`, `primeira` (numeração inicial, ex.
  ENEM 91–180), `num_alternativas` (por questão), `status` (rascunho/aberta/encerrada),
  `codigo_atual`, `codigo_expira_em`, `aparece_na_lista` (bool), `criado_em`.
- **imagens_questao:** `simulado_id`, `numero`, `caminho_no_storage`. (As imagens em si no Storage.)
- **gabaritos:** `simulado_id`, `respostas` (array de letras A–E, vazio, ou `X` = anulada).
  **TABELA TRANCADA**: RLS sem SELECT para aluno nem para outros professores.
- **envios:** `simulado_id`, `nome`, `turma`, `respostas`, `acertos`, `nota`, `quando`.

---

## 7. Segurança (desde o começo)

- **Gabarito nunca vai para o cliente.** A correção é uma função no banco (Postgres, tipo
  `SECURITY DEFINER`) ou Edge Function: recebe as respostas, lê o gabarito internamente, grava o
  envio e devolve só nota + correção (certa/errada/branco/anulada), e a letra certa **só** se a
  preferência estiver ligada (decisão 8).
- **RLS em todas as tabelas** conforme a seção 4.
- **Chave service role só na Edge Function** "criar professor"; nunca no site nem no GitHub.
- **Código da prova:** 6 caracteres (bilhões de combinações; adivinhar é inviável). A entrada na
  prova valida código + janela de tempo numa função, não só no front.
- **Abusos do aluno (sem login):** limitar envios por minuto; o professor pode apagar envios na tela
  de resultados.
- **Excel:** ao exportar, colocar apóstrofo antes de valores que comecem com `= + - @` (evita
  fórmula maliciosa na planilha), como o app atual já faz.

---

## 8. Problemas conhecidos e o que fazer (herdados do estudo anterior)

- **P1 — Tráfego de saída (egress) das imagens:** Supabase grátis ≈ **5 GB/mês** de download. Cada
  aluno baixa as imagens da prova (é "imagens × nº de alunos"). Na escala real (~30 questões WebP,
  ~100 alunos ≈ 300 MB por simulado) dá **~15 simulados/mês** — folgado. Mitigar mesmo assim:
  WebP tamanho-celular, cache longo no navegador (aluno não baixa de novo), e uma tela para o admin
  ver quanto do mês já foi usado. **Preparar o código para trocar o Storage por Cloudflare R2**
  (10 GB, download grátis; pede cartão, sem cobrança no limite) caso cresça.
- **P2 — Espaço (1 GB):** ~130 provas em WebP. Botão para apagar simulados antigos (com as imagens);
  aviso aos 80%.
- **P3 — Projeto "dorme" após ~7 dias sem acesso:** criar um "despertador" grátis (ex.: tarefa
  agendada no GitHub Actions) que acessa o banco 1×/semana; e instrução no guia ("se não abrir após
  as férias, entre no Supabase e clique em Restore").
- **P6 — E-mail limitado no Supabase grátis:** por isso o admin cria o professor com **senha
  provisória** e pode **redefinir** pela tela (sem depender de e-mail).
- **P7 — Criar professor precisa da chave de admin:** fica numa **Edge Function** que só aceita
  pedido do admin logado (seção 7).
- **P9 — Instalar por escola é técnico:** guia passo a passo com imagens; um **script único** que
  monta o banco; mesmo repositório para todas. **Sobra:** quando o banco mudar, aplicar a mudança no
  Supabase de cada escola — prever um passo de "atualização" (script de migração).
- **P10 — LGPD (dados de alunos menores):** conta Supabase **institucional**; aviso de privacidade
  simples na tela do aluno; botão "apagar simulado e envios"; Excel como cópia de segurança; guardar
  só nome, turma, respostas e nota.
- **P5 — Gabarito por foto:** sem o OCR do Google, usar leitor no navegador (ex.: Tesseract.js), que
  erra mais; sempre mostrar o resultado para o professor conferir antes de salvar. (Texto, .txt, PDF
  com texto e Excel continuam ótimos.)

---

## 9. Etapas de construção (incremental)

| Etapa | O que | Entregável |
|---|---|---|
| 0. Preparar | Criar projeto no Supabase (grátis, conta institucional); guardar URL + chave pública; estrutura de pastas nova no repo (ex.: `web/` e `supabase/`) sem tocar no `apps_script/` | Projeto no ar, "Olá mundo" lendo o banco |
| 1. Banco | Tabelas, RLS, função de correção, geração/validação de código + janela, Storage bucket | Banco testável com dados de mentira |
| 2. Aluno | Tela início (aluno/professor), entrar por código/link, lista de provas abertas, responder, enviar, ver nota/correção | Aluno fim-a-fim |
| 3. Professor | Login, novo simulado (PDF → extrator no navegador → WebP → Storage), gabarito, abrir/encerrar (código), resultados ao vivo, Baixar Excel | Professor fim-a-fim |
| 4. Admin | Edge Function criar professor, tela "Professores" (cadastrar, redefinir senha, desativar) | Admin fim-a-fim |
| 5. Publicar/testar | Site no Cloudflare Pages, "despertador", guia de instalação por escola, teste com turma de mentira | Pronto para piloto |
| 6. Melhorias | Revisão das questões antes de publicar, resultados por matéria, tela de uso do mês | — |

**Regra durante a mudança:** o app atual no Google não é desligado até o novo passar num teste com
turma real.

---

## 10. Decisões ainda pendentes (perguntar à dona quando chegar a hora)

1. Por padrão, uma prova publicada **aparece na lista** de provas abertas ou só entra quem tem
   link/código?
2. Quem é o **administrador** e de quem é a **conta institucional** do Supabase?
3. **Nome/endereço** do site (ex.: `simulados-escolax.pages.dev`).
4. Aceita cadastrar **cartão no Cloudflare** para o R2 (sem cobrança no limite) se o tráfego crescer?
5. Duração do **fecho automático** do código (2 h? até o fim do dia?).
