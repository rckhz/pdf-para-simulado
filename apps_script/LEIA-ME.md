# Simulados Online — instalação (feita UMA vez)

Este guia é para quem instala o sistema. O professor não precisa ler isto:
ele só usa dois links.

## O que o sistema faz

| Quem | O que faz |
|---|---|
| **Professor** | Abre a "Área do Professor", escolhe o PDF da prova, clica na resposta certa de cada questão, clica em "Liberar" e manda o link aos alunos. Vê as notas na mesma página. |
| **Aluno** | Abre o link, digita nome e turma, responde e vê a nota na hora. |
| **Sistema** | Recorta as questões do PDF (no navegador do professor), guarda as imagens no Google Drive, corrige sozinho e grava tudo numa planilha. |

## Arquivos desta pasta

| Arquivo | Para quê |
|---|---|
| `Code.gs` | Servidor: senha, planilha, Drive, correção |
| `Professor.html` | Área do Professor |
| `Aluno.html` | Página do aluno |
| `Extrator.html` | O recortador de questões (código Python). **Gerado automaticamente**, não edite à mão |
| `appsscript.json` | Configurações do projeto |

## Passo a passo da instalação

> **Use a conta Google do professor** (ou da escola). As imagens e a planilha de
> notas ficam no Google Drive da conta que fizer a instalação.

1. Entre em <https://script.google.com> com essa conta e clique em **Novo projeto**.
   Dê um nome ao projeto (clique em "Projeto sem título"), por exemplo `Simulados`.
2. **Code.gs**: apague o conteúdo do arquivo `Código.gs` que já vem aberto e cole todo
   o conteúdo de `Code.gs`.
3. **Arquivos HTML**: clique em **+** (ao lado de "Arquivos") → **HTML** e crie três
   arquivos com estes nomes **exatos** (sem `.html`; o editor coloca sozinho):
   `Professor`, `Aluno` e `Extrator`. Em cada um, apague o conteúdo e cole o arquivo
   correspondente desta pasta. O `Extrator` é uma linha só, bem comprida; isso é normal.
4. **Configurações**: clique na engrenagem (**Configurações do projeto**) e marque
   **"Mostrar o arquivo de manifesto appsscript.json no editor"**. Volte ao editor,
   abra `appsscript.json` e cole o conteúdo do arquivo desta pasta.
5. Clique em **Salvar** (ícone de disquete).
6. **Publicar**: clique em **Implantar → Nova implantação**. Na engrenagem, escolha
   **App da Web**. Confira:
   - Executar como: **Eu**
   - Quem pode acessar: **Qualquer pessoa**

   Clique em **Implantar**.
7. O Google vai pedir autorização: **Autorizar acesso** → escolha a conta →
   aparece "O Google não verificou este app" (normal para scripts próprios) →
   **Avançado** → **Acessar Simulados (não seguro)** → **Permitir**.
8. Copie o **URL do app da Web** (termina em `/exec`).

## Os dois links

- **Professor:** o URL + `?professor`
  Exemplo: `https://script.google.com/macros/s/AKfy.../exec?professor`
  No primeiro acesso, ele pede para criar uma senha. Salve esse link nos favoritos
  do professor.
- **Alunos:** o professor não precisa montar link nenhum. Depois de liberar um
  simulado, a Área do Professor mostra o link certo, com botões "Copiar" e "WhatsApp".

A planilha **"Simulados - Resultados"** e a pasta **"Simulados - Imagens das Questões"**
aparecem sozinhas no Drive no primeiro uso.

## Mensagem pronta para mandar ao professor

> Oi! O sistema de simulados está pronto. Salve este link nos favoritos:
> **(link do professor)**
> 1. No primeiro acesso, crie uma senha.
> 2. Clique em **+ Novo simulado** e escolha o PDF da prova. Espere a barra terminar.
> 3. Confira as questões e clique na letra certa de cada uma (o gabarito é salvo sozinho).
> 4. Vá em **Enviar aos alunos** → **Liberar** e mande o link pelo WhatsApp.
> 5. As notas aparecem em **Resultados**.

## Atualizar o sistema depois de alguma mudança

1. Se você mudou o recortador (`src/pdf_para_simulado/extrair_questoes.py`), rode
   `uv run src/pdf_para_simulado/gerar_apps_script.py` para gerar um `Extrator.html` novo.
   Para montar o ZIP do professor: `uv run src/pdf_para_simulado/empacotar.py "<link do app>"`.
2. Cole os arquivos alterados no editor do Apps Script e salve.
3. Se o `appsscript.json` mudou, ou se o código passou a usar algum serviço novo do
   Google: no editor, escolha a função `autorizarPermissoes` no menu do topo, clique
   em **Executar** e aceite as permissões (mesmo caminho: Avançado → Acessar → Permitir).
4. **Implantar → Gerenciar implantações → ✏ (editar) → Versão: Nova versão → Implantar.**
   Assim o link continua o mesmo. Não use "Nova implantação": isso cria um link novo.

## Gabarito por arquivo (foto, PDF ou texto)

Na aba **Gabarito**, o professor pode clicar em **Enviar arquivo do gabarito**:

- **Texto (.txt):** lido direto no navegador. Aceita "01 - A", "1) a", "1.A",
  várias respostas por linha, ou só a sequência de letras ("ABDCA...").
- **Planilha (Excel .xlsx/.xls ou .ods):** o Google converte e todas as abas são lidas
  (o nome da aba, como "6º ano", vira o nome do gabarito).
- **Foto ou PDF:** o texto é lido pelo reconhecimento de texto (OCR) do Google Drive
  (o mesmo do "Abrir com Google Docs"). Para isso o projeto usa o serviço avançado
  **Drive API**, que já vem ativado pelo `appsscript.json`. Se aparecer um erro
  dizendo que ele não está ativado: no editor, em **Serviços** → **+** → **Drive API** → **Adicionar**.

As respostas encontradas são marcadas na tela para o professor conferir. Questões que
não foram encontradas ficam em branco e aparecem num aviso, e respostas repetidas
diferentes (arquivo com o gabarito de mais de uma prova) também são avisadas.

## Problemas comuns

| Problema | Solução |
|---|---|
| Professor esqueceu a senha | No editor, escolha a função `redefinirSenhaProfessor` no menu do topo e clique em **Executar**. No próximo acesso ele cria outra. Nada é apagado. |
| "Não encontrei questões neste PDF" | O PDF não segue o modelo (selos azuis com o número) ou é escaneado. Esse tipo não é lido automaticamente. |
| "Não foi possível preparar o leitor de provas" | O navegador não conseguiu baixar o leitor (cerca de 20 MB, só na primeira vez). Verifique a internet ou se a rede da escola bloqueia `cdn.jsdelivr.net`. |
| Aluno vê "não foi liberado" | O simulado está como Rascunho. Na Área do Professor: aba **Enviar aos alunos** → **Liberar**. |

## Limites do Google (contas gratuitas)

- A turma toda pode responder ao mesmo tempo. Se o Google recusar algum pedido por
  excesso de acessos simultâneos, a página do aluno tenta de novo sozinha.
- Cada simulado ocupa cerca de 5 a 15 MB no Drive (as imagens).
