"""
Extrator de questões de prova (PDF) para o simulado online.

Como usar (na pasta do projeto):
    uv run src/pdf_para_simulado/extrair_questoes.py              -> usa prova.pdf
    uv run src/pdf_para_simulado/extrair_questoes.py outra.pdf    -> usa outro arquivo

O que o programa gera (dentro da pasta "saida/"):
    assets/q01.jpg, q02.jpg, ...   uma imagem por questão
    ANSWER_KEY.js                  gabarito (ou modelo vazio para preencher)
    relatorio.txt                  resumo e avisos para revisão
    simulado_extraido.zip          tudo acima num único arquivo

IDEIA GERAL
-----------
1. Em cada página, achamos as "barreiras" (marcos que iniciam algo novo):
   - SELO: o quadradinho com o número da questão. Usa uma fonte exclusiva
     (SegoeUIBlack, ~6.4 pt), então é seguro procurar pela fonte em vez de
     procurar números soltos no texto.
   - INSTRUÇÃO: "Leia o texto para responder à questão 05." ou "às questões
     05 e 06". Ela inicia um TEXTO DE APOIO que fica ANTES do selo e diz a
     quais questões ele pertence.
   - CABEÇALHO de seção: "LÍNGUA PORTUGUESA", "BLOCO 1 · ...", "FÍSICA"...
2. A página tem duas colunas. Lemos a coluna esquerda de cima para baixo,
   depois a direita. Tudo entre duas barreiras pertence à barreira de cima.
3. Se uma coluna começa com conteúdo ANTES da primeira barreira, esse
   conteúdo é a continuação do que vinha antes (transbordou de coluna/página).
4. Cada imagem final = textos de apoio da questão + a própria questão.
5. Recortamos só a área com conteúdo, mais uma pequena margem, e
   renderizamos direto do PDF (sem distorcer nem perder nitidez).
6. Opcionalmente, tiramos as alternativas analisando os pixels (procura as
   linhas que começam com "(A)", "(B)"...).
"""

import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # é a mesma biblioteca que "fitz"; "import fitz" está sendo descontinuado

# =====================================================================
# CONFIGURAÇÕES (mude aqui)
# =====================================================================

# True  = a imagem mostra o enunciado E as alternativas (o simulado só tem os botões A/B/C/D).
# False = a imagem mostra só o enunciado (para um simulado que escreve as alternativas à parte).
INCLUIR_ALTERNATIVAS = True

# Resolução das imagens geradas (200-300 é um bom intervalo).
DPI = 250
QUALIDADE_JPG = 92  # 0-100

# Margem em branco ao redor do conteúdo, em pontos (72 pontos = 1 polegada).
MARGEM = 6

# Espaço entre trechos empilhados (texto de apoio + questão, ou questão
# que continua em outra coluna/página).
ESPACO_ENTRE_TRECHOS = 10

PASTA_SAIDA = Path("saida")

# Como reconhecer o selo de número da questão.
FONTE_SELO = "SegoeUIBlack"
TAMANHO_SELO = 6.4
TOLERANCIA_TAMANHO = 0.25  # o cartão-resposta usa 6.0 pt, então fica de fora

# Cabeçalhos de seção usam a mesma fonte dos selos, um pouco maior
# (7.1 pt numa prova, 8.2 pt em outra). O título da prova (11.2 pt) fica
# acima do limite máximo e não conta como cabeçalho.
TAMANHO_MIN_CABECALHO = 6.9
TAMANHO_MAX_CABECALHO = 10

# Rodapé: texto que começa nos últimos 5% da altura da página.
FRACAO_RODAPE = 0.95

# Uma instrução de texto de apoio colada logo abaixo do selo (a menos de
# tantos pontos) faz parte da própria questão, e não de um apoio novo.
DISTANCIA_INSTRUCAO_DENTRO = 15

# Análise de pixels para achar as alternativas (só usada se INCLUIR_ALTERNATIVAS = False).
DPI_ANALISE = 200      # resolução usada só para analisar (não é a da imagem final)
LIMIAR_ESCURO = 140    # pixel com cinza < 140 (0=preto, 255=branco) conta como "tinta"


# =====================================================================
# ESTRUTURAS DE DADOS
# =====================================================================
# "dataclass" é um jeito curto de criar uma classe que só guarda dados.

@dataclass
class Item:
    """Qualquer coisa desenhada na página: linha de texto, imagem, desenho, selo."""
    rect: pymupdf.Rect
    tipo: str          # "texto", "imagem", "desenho" ou "selo"
    texto: str = ""
    ancora_y: float = None   # altura usada para decidir a quem o item pertence (padrão: o centro)

    def centro_y(self):
        return self.ancora_y if self.ancora_y is not None else (self.rect.y0 + self.rect.y1) / 2


@dataclass
class Barreira:
    """Algo que marca o início de uma região."""
    tipo: str          # "selo", "instrucao" ou "cabecalho"
    rect: pymupdf.Rect
    numero: int = 0    # número impresso no selo
    texto: str = ""
    alvos: list = None  # instrução: números das questões citadas (None = não diz)
    coluna_x1: float = None  # onde termina a coluna desta questão (linha desenhada embaixo do rótulo)
    prefixo: str = ""  # letras antes do número ("AF" em "AF2.": nível A, Física)


@dataclass
class Trecho:
    """Um retângulo de uma página. Uma questão é formada por 1 ou mais trechos."""
    pagina: int        # índice da página, começando em 0
    rect: pymupdf.Rect
    itens: list = field(default_factory=list)
    tapar: list = field(default_factory=list)   # rodapé que caiu dentro do recorte: pintado de branco


@dataclass
class Questao:
    numero_selo: int
    pagina: int
    rect_selo: pymupdf.Rect
    trechos: list = field(default_factory=list)
    apoios: list = field(default_factory=list)   # objetos Apoio que valem para esta questão
    avisos: list = field(default_factory=list)   # (pagina, mensagem)
    alternativas: str = ""
    assunto: str = ""                            # matéria, tirada do último cabeçalho de seção
    num_alternativas: int = None                 # 4 (A-D) ou 5 (A-E)


@dataclass
class Apoio:
    """Texto de apoio: começa numa instrução "Leia o texto para responder à
    questão 05" e vai até o próximo selo/instrução/cabeçalho."""
    pagina: int
    texto: str
    alvos: list = None          # números das questões que usam este texto
    proxima: int = None         # número do primeiro selo que aparece depois dele
    trechos: list = field(default_factory=list)


@dataclass
class BlocoSolto:
    """Conteúdo entre um cabeçalho de seção e a próxima barreira, sem dono."""
    pagina: int
    trechos: list = field(default_factory=list)
    rascunho: bool = False   # coluna "RASCUNHO": ficar sem dono é o esperado


# =====================================================================
# ETAPA 1: ler cada página e separar barreiras, conteúdo e rodapé
# =====================================================================

PADRAO_INICIO_INSTRUCAO = re.compile(
    r"^\s*(leia|observe|analise|considere|examine|utilize|use|com base|textos?\s+(?:para|comum))\b", re.IGNORECASE)
PADRAO_QUESTOES = re.compile(
    r"\bquest(?:ão|ões|ao|oes)\b\s*((?:de\s+)?(?:n[úu]meros?\s+)?\d{1,2}(?:\s*(?:,|e|a|até|ate)\s*\d{1,2})*)?",
    re.IGNORECASE)


def numeros_citados(texto):
    """'questões 05 e 06' -> [5, 6];  'questões de 15 a 18' -> [15, 16, 17, 18];
    'questão.' (sem número) -> None."""
    m = PADRAO_QUESTOES.search(texto)
    if not m or not m.group(1):
        return None
    # "questões de números 18 e 19" (Etec 2012)
    trecho = re.sub(r"^(?:de\s+)?(?:n[úu]meros?\s+)?", "", m.group(1).strip(), flags=re.IGNORECASE)
    numeros = []
    intervalo = False
    for token in re.findall(r"\d{1,2}|até|ate|\ba\b", trecho, re.IGNORECASE):
        if token.isdigit():
            n = int(token)
            if intervalo and numeros:
                numeros.extend(range(numeros[-1] + 1, n + 1))
            else:
                numeros.append(n)
            intervalo = False
        else:
            intervalo = True   # "a" / "até" -> o próximo número fecha um intervalo
    return numeros


# Instrução sem número de questão (ex.: Prova Paraná): "Leia o texto abaixo.", "Leia os textos abaixo.".
# O texto vale para todas as questões que vêm depois dele, até o próximo texto ou matéria.
PADRAO_LEIA_ABAIXO = re.compile(
    r"^\s*(leia|observe)\s+(o|os|a|as)\s+(textos?|imagens?|tirinhas?|charges?|cartuns?|poemas?|"
    r"fragmentos?|trechos?|gr[áa]ficos?|infogr[áa]ficos?)\s+(abaixo|a\s+seguir)\s*[.:]?\s*$", re.IGNORECASE)


def linhas_visuais(linhas):
    """Quantas linhas de verdade há num bloco: pedaços na mesma altura contam como uma linha
    (texto justificado pode vir em pedaços, ex.: UFRGS, "Instrução: As" + "questões" + "01"...)."""
    alturas = []
    for r, _ in linhas:
        if not any(abs(r.y0 - y) < 3 for y in alturas):
            alturas.append(r.y0)
    return len(alturas)


def e_instrucao(texto, num_linhas):
    """Frase curta do tipo "Leia o texto para responder à questão 05."."""
    if num_linhas == 1 and PADRAO_LEIA_ABAIXO.match(texto):
        return True
    m = PADRAO_QUESTOES.search(texto)
    # Sem número, a palavra "questões" só vale em "responder à questão" / "questões a seguir"
    # (ex.: Fuvest, "...na discussão sobre questões indígenas" é o comando da própria questão).
    if m and not m.group(1) and not re.search(
            r"responder\s+(?:[àa]s?\s+)?quest|quest\w*\s+(?:a\s+seguir|seguintes|abaixo)", texto, re.IGNORECASE):
        m = None
    # "O texto a seguir é um trecho de... Leia-o para responder às questões 5 a 7." (ex.: Cotuca)
    para_responder = re.search(r"\bpara\s+responder\s+(?:[àa]s?\s+)?quest", texto, re.IGNORECASE)
    # "O texto a seguir é referência para as questões 10 a 18." (UFPR) /
    # "AS QUESTÕES 09 A 22 REFEREM-SE ÀS CRÔNICAS..." (UERJ)
    # "Instrução: As questões de 01 a 08 estão relacionadas ao texto abaixo." (UFRGS)
    referencia = re.search(r"refer[êe]ncia\s+(?:para|[àa]s?)\s+(?:as\s+)?quest|"
                           r"quest[õo]es\s+(?:de\s+)?\d{1,3}\s+(?:a|at[ée]|e)\s+\d{1,3}\s+"
                           r"(?:refere|est[ãa]o\s+relacionad|relacionam|baseiam|dizem\s+respeito)",
                           texto, re.IGNORECASE)
    return (num_linhas <= 3 and len(texto) < 220
            and (PADRAO_INICIO_INSTRUCAO.search(texto) is not None
                 or (para_responder or referencia) and m and m.group(1))
            and m is not None)


# Segundo modelo de prova (ex.: Vestibulinho Etec): a questão começa com a linha "Questão 01".
PADRAO_ROTULO = re.compile(r"^quest[ãa]o\s*(\d{1,3})\s*$", re.IGNORECASE)
# Fim das questões (ex.: Fatec, depois da questão 60 vem a redação).
PADRAO_FIM = re.compile(r"\s*(produ[çc][ãa]o\s+de\s+texto|proposta\s+de\s+reda[çc][ãa]o|reda[çc][ãa]o|rascunho(\s+da\s+reda[çc][ãa]o)?)\s*", re.IGNORECASE)
# Começo da parte DISCURSIVA (ex.: OBQ, "Parte II – Questões Analítico-Expositivas"): as questões
# dali em diante são para responder por escrito; o simulado fica só com as de alternativa.
PADRAO_DISCURSIVAS = re.compile(
    r"\s*(?:parte\s+(?:[IVX]{1,4}|\d)\s*[-–—:]?\s*)?(?:quest[õo]es\s+)?(?:anal[íi]tico[-\s]*expositivas|"
    r"discursivas|dissertativas|abertas)\s*|\s*parte\s+(?:discursiva|dissertativa)\s*", re.IGNORECASE)
# Páginas que são só a folha/cartão de respostas, sem questões (ex.: última página).
PADRAO_FOLHA_RESPOSTAS = re.compile(r"folha\s+de\s+respostas|cart[ãa]o[\s-]+resposta", re.IGNORECASE)


def chave_rodape(texto):
    """'VESTIBULAR ∙ Fatec 19' e 'VESTIBULAR ∙ Fatec 6' viram a mesma chave."""
    return re.sub(r"[\d\s]+", "", texto)


_leituras = {"doc": None, "paginas": {}}


def texto_dict(pagina):
    """O mesmo que pagina.get_text("dict"), mas cada página é lida UMA vez só.
    Essa leitura é a parte mais lenta do programa e era repetida várias vezes por página
    (rodapé, cabeçalho, línguas e cada modo de achar questões). O PDF original nunca é
    alterado, então guardar a leitura não muda o resultado."""
    if _leituras["doc"] is not pagina.parent:
        _leituras["doc"] = pagina.parent
        _leituras["paginas"] = {}
    if pagina.number not in _leituras["paginas"]:
        _leituras["paginas"][pagina.number] = pagina.get_text("dict")
    return _leituras["paginas"][pagina.number]


def textos_de_rodape(doc):
    """Textos que se repetem no pé de várias páginas (nome da prova, número da página):
    são rodapé, mesmo quando ficam um pouco acima dos últimos 5% da página."""
    contagem = {}
    for pagina in doc:
        altura = pagina.rect.height
        vistos = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                if texto and linha["bbox"][1] > altura * 0.88:
                    vistos.add(chave_rodape(texto))
        for chave in vistos:
            contagem[chave] = contagem.get(chave, 0) + 1
    minimo = max(3, len(doc) * 0.3)
    # Vale texto com palavras ("VESTIBULAR∙Fatec") ou só o número da página (chave vazia);
    # "(D)", "(E)" no pé de várias páginas são alternativas, não rodapé.
    chaves = {chave for chave, n in contagem.items()
              if n >= minimo and (chave == "" or len(re.findall(r"[A-Za-zÀ-ÿ]", chave)) >= 4)}
    # Texto curto que aparece SEMPRE na mesma altura no pé da folha (ex.: Prova Paraná,
    # "BL01P12_E" e o número da página): também é rodapé. Guardado como ("y", altura).
    alturas = {}
    for pagina in doc:
        vistas = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                if (texto and len(texto) <= 15 and linha["bbox"][1] > pagina.rect.height * 0.88
                        and not re.match(r"^\(?[A-E]\)", texto)):
                    vistas.add(round(linha["bbox"][1], 1))
        for y in vistas:
            alturas[y] = alturas.get(y, 0) + 1
    # Figura ou fio IGUAL, no mesmo lugar, no pé de várias folhas (ex.: Cotuca, o logotipo
    # entre dois fios): também é rodapé. Guardado como ("fig", x0, y0, x1, y1).
    figuras = {}
    for pagina in doc:
        vistas = set()
        rects = [pymupdf.Rect(i["bbox"]) for i in pagina.get_image_info()]
        rects += [d["rect"] for d in pagina.get_drawings()]
        for r in rects:
            if (r.y0 > pagina.rect.height * 0.85 and r.y1 <= pagina.rect.height + 1
                    or r.y1 < pagina.rect.height * 0.12 and r.y0 >= -1):   # (ou no alto: logotipo do cabeçalho)
                vistas.add(("fig",) + tuple(round(v) for v in r))
        for k in vistas:
            figuras[k] = figuras.get(k, 0) + 1
    return (chaves | {("y", y) for y, n in alturas.items() if n >= minimo}
            # (no alto, só o que está em quase todas as folhas: uma caixa "Leia o texto..." pode
            # cair no mesmo lugar em algumas páginas)
            | {k for k, n in figuras.items() if n >= minimo and (k[2] > 300 or n >= len(doc) * 0.6)})


def textos_de_cabecalho(doc):
    """Textos que se repetem no ALTO de várias páginas ("FATEC – FACULDADE DE
    TECNOLOGIA", "TIPO A"...): são cabeçalho da folha, não conteúdo."""
    contagem = {}
    for pagina in doc:
        altura = pagina.rect.height
        vistos = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                if texto and linha["bbox"][3] < altura * 0.12:
                    vistos.add(chave_rodape(texto))
        for chave in vistos:
            contagem[chave] = contagem.get(chave, 0) + 1
    minimo = max(3, len(doc) * 0.3)
    chaves = {chave for chave, n in contagem.items()
              if n >= minimo and len(re.findall(r"[A-Za-zÀ-ÿ]", chave)) >= 4
              and not re.match(r"quest[ãa]o", chave, re.IGNORECASE)}
    # Código curto IGUALZINHO no alto de muitas folhas (ex.: "CE1201", o código do caderno).
    iguais = {}
    for pagina in doc:
        vistos = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                if (re.fullmatch(r"[A-Z]{2,}\d{3,}", texto)
                        and linha["bbox"][3] < pagina.rect.height * 0.12):
                    vistos.add(texto)
        for t in vistos:
            iguais[t] = iguais.get(t, 0) + 1
    # Texto curto sempre no MESMO LUGAR do alto da folha, mas que muda (ex.: UERJ, "Texto Base",
    # "Linguagens", "Matemática" no canto de cima): também é cabeçalho. Guarda a posição.
    lugares = {}
    for pagina in doc:
        vistos = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                x0, y0, x1, y1 = linha["bbox"]
                # (nunca o começo de uma questão no alto da folha: "Questão 01", "01.", "{02}", "(A)"...)
                # (nem letra solta: "R a s c u n h o" escrito letra por letra, ex.: Vunesp)
                if (texto and 3 <= len(re.findall(r"[A-Za-zÀ-ÿ]", texto)) and len(texto) <= 40
                        and y1 < pagina.rect.height * 0.06
                        and not re.match(r"^\s*(quest|\d|\{|\(|[A-E]\)|leia|texto\s+para)", texto, re.IGNORECASE)):
                    vistos.add(("pos", round(x0 / 4), round(y0 / 3)))
                    vistos.add(("pos", 1000 + round(x1 / 4), round(y0 / 3)))
        for k in vistos:
            lugares[k] = lugares.get(k, 0) + 1
    # posições vizinhas contam juntas (a mesma linha pode variar 1 ponto de uma folha para outra)
    vizinhos = lambda k: [("pos", k[1] + dx, k[2] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    lugares = {k: sum(lugares.get(v, 0) for v in vizinhos(k)) for k in lugares}
    # Texto curto IGUALZINHO e no MESMO LUGAR no alto de quase todas as folhas, mesmo com poucas
    # letras (ex.: OBQ, "OBQ 2025" e o "A" de "Modalidade A" embaixo do título).
    identicos = {}
    for pagina in doc:
        vistos = set()
        for bloco in texto_dict(pagina)["blocks"]:
            for linha in bloco.get("lines", []):
                texto = "".join(sp["text"] for sp in linha["spans"]).strip()
                x0, y0, x1, y1 = linha["bbox"]
                if (texto and len(texto) <= 20 and y1 < pagina.rect.height * 0.1
                        and not re.match(r"^\s*(quest|\{|\()", texto, re.IGNORECASE)):
                    vistos.add((texto, round(x0 / 4), round(y0 / 3)))
        for k in vistos:
            identicos[k] = identicos.get(k, 0) + 1
    muitas = max(3, len(doc) * 0.6)
    return chaves | {chave_rodape(t) for t, n in iguais.items() if n >= minimo} | {
        k for k, n in lugares.items() if n >= minimo} | {
        chave_rodape(k[0]) for k, n in identicos.items() if n >= muitas and chave_rodape(k[0])}


def analisar_pagina(pagina, modo="selo", rodape=frozenset(), cabecalho=frozenset(), sem_negrito=False):
    """Devolve um dicionário com as barreiras, os itens de conteúdo, as linhas
    divisórias de coluna e o limite inferior útil (topo do rodapé) da página.

    modo="selo"   -> questões marcadas por um selo com o número (SARESP, Provão...), 2 colunas
    modo="rotulo" -> questões marcadas pela linha "Questão 01" (Vestibulinho...), 1 coluna
    modo="numero" -> questões que começam com o número em negrito ("6. Considere..."),
                     em 1 ou 2 colunas (ex.: Fatec 2025)
    sem_negrito=True -> no modelo "numero", aceita o número sem negrito (ex.: Cotuca)"""
    altura = pagina.rect.height
    barreiras = []
    itens = []
    topo_rodape = altura
    linhas_rodape = []
    fundo_cabecalho = 0   # modelo "numero": fim do cabeçalho repetido no alto da folha

    largura_pag = pagina.rect.width
    desenhos, faixas_cor = [], []
    escondidos = []   # marcas da folha (não são conteúdo): pintadas de branco se caírem num recorte
    marcas_borda = []  # marquinhas que saem pela borda da folha: pintadas de branco mesmo encostando numa figura
    # Seta "►" que marca a alternativa certa (caderno com gabarito, ex.: UFPR): some da imagem.
    marcas_borda += [pymupdf.Rect(r) for r in pagina.search_for("►")]
    def separar(desenhos_pdf):
        """Um "desenho" feito só de retângulos bem afastados entre si (ex.: ONC 2017, a lua
        preta da questão 13 e o chão da 15 guardados juntos) vira um desenho por retângulo."""
        for d in desenhos_pdf:
            rets = [pymupdf.Rect(it[1]) for it in d["items"] if it[0] == "re"]
            if len(rets) >= 2 and len(rets) == len(d["items"]):
                soma = sum(r.width * r.height for r in rets)
                if soma < 0.5 * d["rect"].width * d["rect"].height:
                    for r in rets:
                        yield dict(d, rect=r)
                    continue
            yield d

    for d in separar(pagina.get_drawings()):
        r = d["rect"]
        # Sangria (desenho que passa da borda da folha: fundo, aba colorida na lateral) e
        # marquinhas de registro coladas na borda (ex.: ENEM) não são conteúdo.
        if r.x0 < -1 or r.y0 < -1 or r.x1 > largura_pag + 1 or r.y1 > altura + 1:
            if r.width < 60 or r.height < 60:   # aba na lateral (não o fundo da página inteira)
                escondidos.append(pymupdf.Rect(r))
                if r.width < 30 and r.height < 30:
                    marcas_borda.append(pymupdf.Rect(r) & pagina.rect)
            continue
        if r.width < 15 and r.height < 15 and (r.x1 < 24 or r.x0 > largura_pag - 24):
            escondidos.append(pymupdf.Rect(r))
            continue
        # Fundo colorido da folha inteira (ex.: OBMEP): não é conteúdo de questão nenhuma.
        if r.width > largura_pag * 0.85 and r.height > altura * 0.85:
            continue
        # Fio de ponta a ponta no alto ou no pé da folha (cabeçalho/rodapé, ex.: Fuvest).
        if r.height < 3 and r.width > largura_pag * 0.97 and (r.y1 < altura * 0.06 or r.y0 > altura * 0.94):
            escondidos.append(pymupdf.Rect(r))
            continue
        desenhos.append(r)
        fill = d.get("fill")
        if fill and min(fill) < 0.97 and 10 < r.height < 60 and r.width > 150:
            faixas_cor.append(r)
    # A linha fina vertical que separa as colunas não é conteúdo.
    # Uma figura larga pode "cortar" a linha em pedaços curtos; por isso também conta
    # qualquer linha fina vertical bem no meio da página.
    meio = pagina.rect.width / 2
    # No modelo "Questão 01" só vale a linha alta (um pontilhado curto entre um gráfico e as
    # alternativas, dentro da questão, não separa colunas).
    divisorias = [r for r in desenhos if r.width < 3 and r.height > altura * 0.4] if modo == "rotulo" else [
        r for r in desenhos if r.width < 3 and (r.height > 200 or abs((r.x0 + r.x1) / 2 - meio) < 6)]
    if modo == "numero":
        # O lado de uma caixa ou de uma tabela (linha horizontal ligada no topo E no pé dela)
        # não separa colunas (ex.: Prova Paraná, poema dentro de um quadro).
        def ligada(r, y):
            x = (r.x0 + r.x1) / 2
            return any(d.height < 3 and d.width > 20 and abs(d.y0 - y) < 3 and d.x0 - 2 <= x <= d.x1 + 2
                       for d in desenhos)
        # Tracinho curto no meio da folha (traço de uma fórmula, lado de uma figura) também não.
        # (A linha que desce a folha quase toda bem no meio, ligada no fio do cabeçalho e no do
        # rodapé, é divisória de verdade: ex.: Fuvest.)
        alta_no_meio = lambda r: r.height > altura * 0.6 and abs((r.x0 + r.x1) / 2 - meio) < 15
        divisorias = [r for r in divisorias if r.height > 60 and (
            alta_no_meio(r) or not (ligada(r, r.y0) and ligada(r, r.y1)))]

    # Figura/fio repetido no pé de várias folhas (ver textos_de_rodape): o rodapé começa nele.
    # No alto da folha (logotipo do cabeçalho, ex.: Unicamp): só some da imagem.
    figs_cabecalho = []
    for r in [pymupdf.Rect(i["bbox"]) for i in pagina.get_image_info()] + desenhos:
        if ("fig",) + tuple(round(v) for v in r) in rodape:
            # Marquinha pequena na margem (ex.: ENEM, o quadradinho preto de registro na
            # altura da última alternativa): só some, sem encurtar a página.
            if r.width < 15 and r.height < 15:
                escondidos.append(pymupdf.Rect(r))
            elif r.y0 > altura / 2:
                topo_rodape = min(topo_rodape, r.y0)
                linhas_rodape.append(pymupdf.Rect(r))
            else:
                figs_cabecalho.append(pymupdf.Rect(r))
                escondidos.append(pymupdf.Rect(r))

    # --- Texto ---
    # get_text("dict") devolve blocos -> linhas -> "spans" (trechos com a mesma fonte).
    blocos_texto = []   # linhas "normais" de cada bloco, para procurar instruções no fim
    blocos = texto_dict(pagina)["blocks"]
    # Capa com as instruções da prova ("INSTRUÇÕES PARA A REALIZAÇÃO DA PROVA" / "1. Você
    # recebeu..."): os itens numerados dela não são questões.
    capa = modo == "numero" and any(
        re.fullmatch(r"INSTRU[ÇC][ÕO]ES\b.*", "".join(sp["text"] for sp in l["spans"]).strip())
        and l["bbox"][1] < altura * 0.4 for b in blocos for l in b.get("lines", []))
    # Mas a capa pode ter questões embaixo das instruções (ex.: OBMEP): o número da questão é
    # bem maior que o dos itens das instruções, e esse continua valendo.
    # Tamanho de letra mais comum na folha (o do texto das questões).
    contagem_tam = {}
    for b in blocos:
        for l in b.get("lines", []):
            for sp in l["spans"]:
                contagem_tam[round(sp["size"])] = contagem_tam.get(round(sp["size"]), 0) + len(sp["text"].strip())
    tamanho_texto = max(contagem_tam, key=contagem_tam.get) if contagem_tam else 0
    tamanho_capa = None
    if capa:
        tamanhos = [l["spans"][0]["size"] for b in blocos for l in b.get("lines", [])
                    if re.match(r"^\d{1,3}\.(?:\s|$)", "".join(sp["text"] for sp in l["spans"]).strip())]
        tamanho_capa = min(tamanhos, default=0) + 1.5

    # Cabeçalho repetido no alto da folha (logotipo, código de barras, "ENEM2025..."):
    # tudo até o fim dele fica de fora.
    if modo in ("rotulo", "numero"):
        for b in blocos:
            for l in b.get("lines", []):
                t = "".join(sp["text"] for sp in l["spans"]).strip()
                if t and l["bbox"][3] < altura * 0.12 and (chave_rodape(t) in cabecalho or any(
                        ("pos", x + dx, round(l["bbox"][1] / 3) + dy) in cabecalho
                        for x in (round(l["bbox"][0] / 4), 1000 + round(l["bbox"][2] / 4))
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1))):
                    fundo_cabecalho = max(fundo_cabecalho, l["bbox"][3])
        # Número da folha no alto, na mesma altura do cabeçalho (ex.: OBMEP, "2" numa caixinha).
        if fundo_cabecalho:
            for b in blocos:
                for l in b.get("lines", []):
                    t = "".join(sp["text"] for sp in l["spans"]).strip()
                    if (t.isdigit() and abs(int(t) - (pagina.number + 1)) <= 3
                            and l["bbox"][1] < fundo_cabecalho and l["bbox"][3] < fundo_cabecalho + 10):
                        fundo_cabecalho = max(fundo_cabecalho, l["bbox"][3])

    # Modelo "Questão 01" com a palavra e o número SEPARADOS (ex.: "Questão" + "01" numa
    # caixinha ao lado, em outra fonte). Junta a palavra com o número que está à direita,
    # na mesma altura. Se houver mais de um número ali (ex.: um "00" escondido sob o "26"),
    # vale o maior.
    rotulo_partido, consumidas = {}, set()
    if modo == "rotulo":
        todas = [(linha_justa(l), "".join(sp["text"] for sp in l["spans"]).strip())
                 for b in blocos if b["type"] == 0 for l in b["lines"]]
        for r, t in todas:
            if not re.fullmatch(r"quest[ãa]o", t, re.IGNORECASE):
                continue
            ao_lado = [(r2, t2) for r2, t2 in todas
                       if t2.isdigit() and len(t2) <= 4 and r.x1 - 2 <= r2.x0 <= r.x1 + 40
                       and min(r.y1, r2.y1) - max(r.y0, r2.y0) > 0.5 * min(r.height, r2.height)]
            # Número grande logo embaixo da palavra (ex.: UERJ, "Questão" pequeno em cima e
            # "01" enorme, alinhados à esquerda).
            ao_lado += [(r2, t2) for r2, t2 in todas
                        if t2.isdigit() and len(t2) <= 3 and r2.height > 2 * r.height
                        and abs(r2.x0 - r.x0) < 10 and r.y0 - 2 <= r2.y0 <= r.y1 + 4]
            valores = [int(t2) for _, t2 in ao_lado if int(t2) > 0]
            if valores:
                uniao = pymupdf.Rect(r)
                for r2, _ in ao_lado:
                    uniao |= r2
                    consumidas.add(tuple(r2))
                rotulo_partido[tuple(r)] = (max(valores), uniao)

    retangulos_linhas = [linha_justa(l) for b in blocos if b["type"] == 0 for l in b["lines"]
                         if "".join(sp["text"] for sp in l["spans"]).strip()]

    def titulo_falso(r, so_mesma_linha=False, faixa=None):
        """Palavra em maiúsculas que não é título de matéria: tem outro texto na mesma altura
        (ex.: uma linha de alternativas) ou é a legenda de uma figura pequena (ex.: Canguru,
        "ENTRADA" dentro do desenho de um prédio)."""
        # (com uma faixa, só conta o texto dentro dela: ex.: Fatec, "GEOGRAFIA" no alto da coluna
        # direita fica na altura de "Questão 37" da esquerda)
        if any(o != r and min(o.y1, r.y1) - max(o.y0, r.y0) > 0.5 * min(o.height, r.height)
               and (faixa is None or (faixa.x0 - 2 <= o.x0 and o.x1 <= faixa.x1 + 2))
               for o in retangulos_linhas):
            return True
        if so_mesma_linha:
            return False
        # Última linha de um parágrafo (ex.: UEL, "...Companhia Colonizadora do Tibagy" + "LTDA."):
        # começa onde começa a linha de cima, colada nela, e a de cima é bem mais comprida.
        if any(o != r and abs(o.x0 - r.x0) < 3 and 0 <= r.y0 - o.y1 < 0.6 * r.height
               and o.width > 2 * r.width for o in retangulos_linhas):
            return True
        # Figura ao lado, na mesma altura (ex.: OBA, legenda ao lado do desenho do globo).
        # Uma faixa atrás do título (que contém o texto) não conta.
        figuras = [pymupdf.Rect(i["bbox"]) for i in pagina.get_image_info()] + [
            d for d in desenhos if d.width > 15 and d.height > 15]
        centro = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if any(min(f.y1, r.y1) - max(f.y0, r.y0) > 0 and not f.contains(centro)
               and f.width < pagina.rect.width * 0.8 and (f.x1 < r.x0 or f.x0 > r.x1)
               for f in figuras):
            return True
        # Texto escrito em cima de uma imagem pequena (ex.: OBA, "PLANO DA ÓRBITA / DA TERRA"
        # numa etiqueta branca no meio do desenho): é legenda.
        if any(pymupdf.Rect(i["bbox"]).contains(centro) and pymupdf.Rect(i["bbox"]).width < pagina.rect.width * 0.6
               for i in pagina.get_image_info()):
            return True
        junto = juntar_desenhos_que_tocam(r, desenhos)
        # título numa caixa fica no meio dela; legenda de figura fica num canto do desenho
        fora_do_meio = abs((junto.y0 + junto.y1) / 2 - (r.y0 + r.y1) / 2) > 0.2 * junto.height
        return junto.width < pagina.rect.width * 0.4 and (junto.height > 2.5 * r.height or fora_do_meio)

    for bloco in blocos:
        if bloco["type"] != 0:   # tipo 0 = texto (imagens tratamos abaixo)
            continue
        linhas_normais = []
        anterior = None   # linha de cima, no mesmo bloco (se não for rodapé)
        for linha in bloco["lines"]:
            texto_linha = "".join(s["text"] for s in linha["spans"]).strip()
            if not texto_linha:
                continue
            rect_linha = linha_justa(linha)
            # Continua uma lista logo acima, colada e alinhada (ex.: OBMEP Mirim, a alternativa
            # (E) bem no pé da folha): é conteúdo, não rodapé.
            continua_lista = (anterior is not None and -4 < rect_linha.y0 - anterior.y1 < 4
                              and abs(rect_linha.x0 - anterior.x0) < 3 and chave_rodape(texto_linha) not in rodape)
            anterior = None
            primeiro = linha["spans"][0]

            # Rodapé ("Simulado ... 1/11"): não faz parte de questão nenhuma.
            # Um número sozinho (chave vazia) só é número de página bem no pé da folha;
            # mais acima pode ser o eixo de um gráfico ("250", "200"...).
            # E precisa ser perto do número da folha (ex.: ENEM, o "90" de uma fração "46/90"
            # na última alternativa não é número de página).
            chave = chave_rodape(texto_linha)
            numero_da_folha = chave != "" or (texto_linha.strip().isdigit()
                                              and abs(int(texto_linha) - (pagina.number + 1)) <= 3)
            if (rect_linha.y0 > altura * FRACAO_RODAPE and not continua_lista) or (
                    rect_linha.y0 > altura * (0.93 if chave == "" else 0.88) and chave in rodape
                    and numero_da_folha) or (
                    rect_linha.y0 > altura * 0.88 and len(texto_linha) <= 15
                    and ("y", round(linha["bbox"][1], 1)) in rodape):
                topo_rodape = min(topo_rodape, rect_linha.y0)
                linhas_rodape.append(pymupdf.Rect(linha["bbox"]))
                continue
            anterior = rect_linha

            if fundo_cabecalho and rect_linha.y1 <= fundo_cabecalho + 2:
                escondidos.append(rect_linha)
                continue
            # Texto minúsculo (marca d'água "ENEM2025ENEM2025..." de 1,5 pt) não é conteúdo.
            if max(sp["size"] for sp in linha["spans"]) < 3:
                escondidos.append(rect_linha)
                continue

            # Redação / rascunho no fim do caderno: daqui para a frente não há questões.
            if modo in ("rotulo", "numero") and PADRAO_FIM.fullmatch(texto_linha):
                # No modelo "numero", um "RASCUNHO" sozinho é só uma coluna em branco.
                tipo = "rascunho" if modo == "numero" and texto_linha.strip().lower() == "rascunho" else "fim"
                rect_fim = rect_linha if modo == "rotulo" else juntar_desenhos_que_tocam(rect_linha, desenhos)
                barreiras.append(Barreira(tipo, rect_fim, texto=texto_linha))
                continue
            if modo in ("rotulo", "numero") and PADRAO_DISCURSIVAS.fullmatch(texto_linha) and (
                    primeiro["flags"] & 16 or re.search(r"bold|black|heavy", primeiro["font"], re.I)):
                barreiras.append(Barreira("fim", rect_linha, texto="discursivas: " + texto_linha))
                continue

            # Modelo "6. Considere...": a linha começa com o número da questão em negrito.
            if modo == "numero":
                # Também com a letra do nível na frente: "A5." (ONC 2019), "EQ3." (ONC 2025).
                # Com letra(s) na frente, o ponto pode faltar: "A12  Para os..." (ONC 2018),
                # e pode vir a matéria junto do nível: "AF2." (Física), "AH2." (História) (ONC 2020/21).
                # (com letras na frente, o texto pode vir colado no ponto: 'AH4.“Durante...')
                m = re.match(r"^(?:[A-E][A-Z]?(?=\d{1,3}(?:\.(?!\d)|\s|$))|(?=\d{1,3}\.(?:\s|$)))"
                             r"(\d{1,3})(?:\.(?!\d)|(?=\s|$))", texto_linha)
                negrito = primeiro["flags"] & 16 or re.search(r"bold|black|heavy", primeiro["font"], re.I)
                # UFPR: "02 - Na figura ao lado..." (número com 2 ou 3 dígitos e hífen).
                m_hifen = re.match(r"^(\d{2,3})\s+[-–]\s+\S", texto_linha)
                if m_hifen and not m:
                    m = m_hifen
                # Prova Paraná: "001) (PS25-LP-024) Em relação..." (número com 3 dígitos e parêntese).
                m_par = re.match(r"^(\d{3})\)(?:\s|$)", texto_linha)
                if m_par and int(m_par.group(1)) > 0:
                    m, negrito = m_par, True
                # Fuvest: "{02}" sozinho na linha marca a questão; "#####" marca o fim dela.
                m_chave = re.fullmatch(r"\{(\d{1,3})\}", texto_linha)
                if m_chave:
                    m, negrito = m_chave, True
                # OBA: "Questão 2) (1 ponto) Os astrônomos..." marca a questão; a linha
                # "2) - Nota obtida: ____" é para o corretor e não aparece na imagem.
                # (também "Questão 1. Suponha..." / "Questão 2." com ponto, ex.: OBF, OBQ)
                m_oba = re.match(r"^quest[ãa]o\s+(\d{1,3})\s*(?:\)|\.(?!\d))", texto_linha, re.IGNORECASE)
                if m_oba and negrito:
                    m = m_oba
                # Número SOZINHO na linha, com o enunciado começando logo embaixo, alinhado
                # com ele (ex.: UEL, "1" e, na linha de baixo, "O girassol apresenta...").
                # (não vale coluna de números, como a numeração das linhas de um texto)
                if (not m and negrito and re.fullmatch(r"\d{1,3}", texto_linha)
                        and primeiro["size"] >= tamanho_texto * 0.95):
                    altura_l = rect_linha.height
                    for b2 in blocos:
                        for l2 in b2.get("lines", []):
                            t2 = "".join(sp["text"] for sp in l2["spans"]).strip()
                            r2 = pymupdf.Rect(l2["bbox"])
                            if (t2 and not re.fullmatch(r"[\d\s.,]+", t2) and abs(r2.x0 - rect_linha.x0) < 4
                                    and 0 <= r2.y0 - rect_linha.y1 < altura_l * 0.8
                                    and len(re.findall(r"[A-Za-zÀ-ÿ]", t2)) >= 3):
                                m = re.fullmatch(r"(\d{1,3})", texto_linha)
                                break
                        if m:
                            break
                if re.match(r"^\d{1,3}\)\s*-\s*nota\s+obtida", texto_linha, re.IGNORECASE):
                    escondidos.append(rect_linha)
                    continue
                if re.fullmatch(r"#{4,}", texto_linha):
                    escondidos.append(rect_linha)
                    continue
                if m and (negrito or sem_negrito) and int(m.group(1)) > 0 and (
                        not capa or primeiro["size"] > tamanho_capa):
                    barreiras.append(Barreira("selo", rect_linha, numero=int(m.group(1)),
                                              prefixo=texto_linha[:m.start(1)]))
                    itens.append(Item(rect_linha, "texto", texto_linha))
                    continue
                # Título de matéria: "LÍNGUA PORTUGUESA", "MATEMÁTICA" (negrito, maiúsculas, centralizado).
                letras = re.sub(r"[^A-Za-zÀ-ÿ]", "", texto_linha)
                materia = re.fullmatch(r"ci[êe]ncias\s+[\wÀ-ÿ]+\s*[–-]\s*[\wÀ-ÿ ]+|l[íi]ngua\s+[\wÀ-ÿ]+|matem[áa]tica",
                                       texto_linha, re.IGNORECASE)   # "Ciências Humanas – Geografia" (Etec)
                # (letra menor que a do texto não é título: legenda de figura ou tabela, ex.: OBMEP Mirim)
                # Canguru: "Problemas de 3 pontos" separa as partes da prova (é título, não conteúdo).
                pontos = re.fullmatch(r"(problemas|quest[õo]es)\s+de\s+\d\s+pontos", texto_linha, re.IGNORECASE)
                if (negrito and len(letras) >= 4 and (letras.upper() == letras or materia or pontos)
                        and max(sp["size"] for sp in linha["spans"]) >= tamanho_texto * 0.9
                        and (pontos or not re.search(r"\d", texto_linha)) and len(texto_linha.split()) <= 5
                        and (pontos or abs((rect_linha.x0 + rect_linha.x1) / 2 - pagina.rect.width / 2) < 60)
                        and not re.match(r"^\(?[A-E]\)", texto_linha)   # alternativa "(C) RSS" (Canguru)
                        and (pontos or not titulo_falso(rect_linha))):
                    rect_cab = juntar_desenhos_que_tocam(rect_linha, desenhos)
                    if letras.upper() != letras:   # título sem caixa (Etec): vale a largura toda da folha
                        rect_cab = pymupdf.Rect(0, rect_cab.y0, pagina.rect.width, rect_cab.y1)
                    texto_cab = texto_linha
                    # Prova Paraná: caixa "ATENÇÃO!" + "Agora, você vai responder a questões de
                    # Matemática." A matéria é o que vem depois de "questões de".
                    if re.fullmatch(r"aten[çc][ãa]o\s*!?", texto_linha, re.IGNORECASE):
                        for b2 in blocos:
                            for l2 in b2.get("lines", []):
                                t2 = "".join(sp["text"] for sp in l2["spans"]).strip()
                                m2 = re.search(r"quest[õo]es\s+de\s+(.+?)\.?$", t2, re.IGNORECASE)
                                y2 = linha_justa(l2).y0
                                if m2 and rect_cab.y0 <= y2 <= rect_cab.y1 + 30:
                                    texto_cab = m2.group(1)
                                    # a frase (e a caixa dela) também é título: não entra nas imagens
                                    rect_cab = juntar_desenhos_que_tocam(rect_cab | linha_justa(l2), desenhos)
                    barreiras.append(Barreira("cabecalho", rect_cab, texto=texto_cab))
                    continue

            # Modelo "Questão 01": a própria linha é o rótulo da questão
            # (ou a palavra "Questão" com o número separado ao lado).
            if tuple(rect_linha) in consumidas:
                continue   # é o número de um rótulo "Questão" + "01": já entrou no selo
            rotulo = PADRAO_ROTULO.match(texto_linha) if modo == "rotulo" else None
            numero_rotulo = int(rotulo.group(1)) if rotulo else None
            if tuple(rect_linha) in rotulo_partido:
                numero_rotulo, rect_linha = rotulo_partido[tuple(rect_linha)]
            if numero_rotulo:
                rect = juntar_desenhos_que_tocam(rect_linha, desenhos)
                # Enfeites na mesma linha do rótulo (etiqueta colorida antes e barra depois,
                # desenhada ou em imagem, ex.: Vunesp): fazem parte do rótulo.
                enfeites = desenhos + [pymupdf.Rect(i["bbox"]) for i in pagina.get_image_info()]
                mudou = True
                while mudou:
                    mudou = False
                    for d in enfeites:
                        if (d.height < 25 and d.width > 2 and abs((d.y0 + d.y1) / 2 - (rect.y0 + rect.y1) / 2) < 6
                                and d.x0 <= rect.x1 + 10 and d.x1 >= rect.x0 - 10
                                and not any(min(d.x1, rect.x1) - 1 <= (v.x0 + v.x1) / 2 <= max(d.x0, rect.x0) + 1
                                            for v in divisorias)):   # sem passar para a outra coluna
                            novo = rect | d
                            if novo != rect:
                                rect, mudou = novo, True
                # o mesmo rótulo repetido no mesmo lugar (acontece em alguns PDFs) conta uma vez só
                if not any(b.tipo == "selo" and b.numero == numero_rotulo and b.rect.intersects(rect)
                           for b in barreiras):
                    # A linha horizontal logo abaixo de "Questão NN" mostra a largura da coluna.
                    linhas_baixo = [d.x1 for d in desenhos
                                    if d.height < 3 and d.width > 100 and rect.y1 - 4 <= d.y0 <= rect.y1 + 8
                                    and d.x0 <= rect.x0 + 15 and d.x1 > rect.x1]
                    barreiras.append(Barreira("selo", rect, numero=numero_rotulo,
                                              coluna_x1=max(linhas_baixo) if linhas_baixo else None))
                    itens.append(Item(rect, "selo", texto_linha))
                continue

            fonte_selo = FONTE_SELO in primeiro["font"] and modo == "selo"
            # Selo de questão: fonte exclusiva + tamanho certo + só dígitos (1 ou 2).
            if (fonte_selo and abs(primeiro["size"] - TAMANHO_SELO) <= TOLERANCIA_TAMANHO
                    and texto_linha.isdigit() and len(texto_linha) <= 2):
                # O selo tem um retângulo azul atrás do número; usamos ele inteiro.
                rect = juntar_desenhos_que_tocam(rect_linha, desenhos)
                barreiras.append(Barreira("selo", rect, numero=int(texto_linha)))
                itens.append(Item(rect, "selo", texto_linha))
                continue

            # Cabeçalho de seção: mesma família de fonte, um pouco maior.
            if fonte_selo and TAMANHO_MIN_CABECALHO <= primeiro["size"] <= TAMANHO_MAX_CABECALHO:
                rect = juntar_desenhos_que_tocam(rect_linha, desenhos)
                barreiras.append(Barreira("cabecalho", rect, texto=texto_linha))
                continue

            # Modelo "Questão 01": título de seção em negrito dentro de uma faixa colorida
            # (ENEM: "LINGUAGENS, CÓDIGOS..." / "Questões de 01 a 05 (opção espanhol)").
            # "Texto para as Questões de 06 a 10." numa faixa igual é instrução, não título.
            dentro = lambda f, r: f.x0 - 1 <= r.x0 and r.x1 <= f.x1 + 1 and f.y0 <= (r.y0 + r.y1) / 2 <= f.y1
            faixa = next((f for f in faixas_cor if dentro(f, rect_linha)), None)
            if (modo == "rotulo" and faixa is not None and faixa.width > rect_linha.width + 40
                    and (primeiro["flags"] & 16 or re.search(r"bold|black|heavy", primeiro["font"], re.I))):
                negrita = lambda sp: sp["flags"] & 16 or re.search(r"bold|black|heavy", sp["font"], re.I)
                na_faixa = [(linha_justa(l), "".join(sp["text"] for sp in l["spans"]).strip(), l)
                            for b2 in blocos if b2["type"] == 0 for l in b2["lines"]
                            if dentro(faixa, linha_justa(l))]
                na_faixa = [x for x in na_faixa if x[1]]
                textos = [t for _, t, _ in na_faixa]
                # Título de seção: só linhas em negrito, encostadas na esquerda da faixa, uma
                # embaixo da outra. (O cabeçalho colorido de uma TABELA tem várias células lado
                # a lado ou texto centralizado: não é título.)
                titulo = all(r.x0 - faixa.x0 < 12 and all(negrita(sp) for sp in l["spans"] if sp["text"].strip())
                             for r, _, l in na_faixa)
                if titulo and not PADRAO_INICIO_INSTRUCAO.search(textos[0] if textos else ""):
                    if not any(b.tipo == "cabecalho" and b.rect == faixa for b in barreiras):
                        principal = next((t for t in textos if not re.match(r"quest", t, re.I)), None)
                        barreiras.append(Barreira("cabecalho", faixa, texto=principal or " ".join(textos)))
                    continue

            # ENEM sem faixa colorida (ex.: 2024): títulos de seção em negrito, soltos na coluna
            # ("LINGUAGENS, CÓDIGOS E SUAS TECNOLOGIAS", "Questões de 01 a 05 (opção inglês)").
            if (modo == "rotulo" and (primeiro["flags"] & 16 or re.search(r"bold|black|heavy", primeiro["font"], re.I))
                    and (re.fullmatch(r"quest[õo]es\s+de\s+\d{1,3}\s+a\s+\d{1,3}\b.*", texto_linha, re.IGNORECASE)
                         or texto_linha.isupper() and texto_linha.endswith("TECNOLOGIAS"))):
                barreiras.append(Barreira("cabecalho", rect_linha, texto=texto_linha))
                continue

            # Modelo "Questão 01": título de matéria ("INGLÊS", "GEOGRAFIA") escrito no meio de
            # uma faixa desenhada bem mais larga que ele.
            if modo == "rotulo":
                letras = re.sub(r"[^A-Za-zÀ-ÿ]", "", texto_linha)
                if (len(letras) >= 4 and letras.upper() == letras and not re.search(r"\d", texto_linha)
                        and len(texto_linha.split()) <= 4):
                    faixa = juntar_desenhos_que_tocam(rect_linha, desenhos)
                    if (faixa.width > 3 * rect_linha.width and faixa.height < 40 and not titulo_falso(rect_linha, True, faixa)
                            and abs((faixa.x0 + faixa.x1) / 2 - (rect_linha.x0 + rect_linha.x1) / 2) < 15):
                        barreiras.append(Barreira("cabecalho", faixa, texto=texto_linha))
                        continue

            linhas_normais.append((rect_linha, texto_linha))

        if not linhas_normais:
            continue
        # Um bloco pode juntar o fim de uma questão com a instrução seguinte (ex.: Cotuca,
        # "e) ...jovens." + "Leia o texto ... questões 10 e 11."): separa onde a instrução começa.
        # Uma linha que sozinha já é a instrução completa (ex.: Fuvest 2027, "Texto para as
        # questões 01 e 02" no mesmo bloco do poema) também fica num bloco só dela.
        # (só se a linha COMEÇA a instrução; a 2ª linha de "Leia a fábula..., / para responder às
        # questões de números 02 a 04" fica junto da 1ª, ex.: Provão Paulista)
        sozinha = lambda t: (bool(PADRAO_INICIO_INSTRUCAO.match(t) or re.match(
            r"\s*(?:os?\s+textos?\s+a\s+seguir|as\s+quest|(?-i:Para)\s+responder)", t, re.IGNORECASE))
            and e_instrucao(t, 1) and numeros_citados(t))
        inicio = 0
        for k in range(1, len(linhas_normais)):
            if ((PADRAO_INICIO_INSTRUCAO.match(linhas_normais[k][1])
                    and linhas_normais[k - 1][1].rstrip().endswith((".", ":", "?", "!")))
                    or sozinha(linhas_normais[k][1]) or sozinha(linhas_normais[k - 1][1])):
                blocos_texto.append(linhas_normais[inicio:k])
                inicio = k
        blocos_texto.append(linhas_normais[inicio:])
        # A instrução também é conteúdo (aparece na imagem).
        for r, t in linhas_normais:
            itens.append(Item(r, "texto", t))

    # Instruções ("Leia o texto para responder às questões 05 e 06"). Ela pode ocupar
    # mais de uma linha e até ser quebrada em DOIS blocos de texto colados um no outro
    # ("Leia o trecho da canção X," + "para responder às questões de 21 a 24.").
    blocos_texto.sort(key=lambda b: (b[0][0].y0, b[0][0].x0))
    usados = set()
    for i, linhas in enumerate(blocos_texto):
        if i in usados:
            continue
        candidatos = [linhas]
        if i + 1 < len(blocos_texto):
            prox = blocos_texto[i + 1]
            # o próximo bloco começa logo abaixo (ou encosta) e não é, sozinho, uma instrução
            # (ex.: Cotuca, "e) ...jovens." colada em "Leia o texto ... questões 10 e 11.")
            if (-6 < prox[0][0].y0 - linhas[-1][0].y1 < 8
                    and not e_instrucao(" ".join(t for _, t in prox), linhas_visuais(prox))):
                candidatos.append(linhas + prox)
        for k, junto in enumerate(candidatos):
            texto_bloco = " ".join(t for _, t in junto)
            if e_instrucao(texto_bloco, linhas_visuais(junto)):
                rect = pymupdf.Rect(junto[0][0])
                for r, _ in junto[1:]:
                    rect |= r
                if modo == "rotulo":   # a faixa/caixa desenhada atrás da frase diz a largura dela
                    rect = juntar_desenhos_que_tocam(rect, desenhos)
                barreiras.append(Barreira("instrucao", rect, texto=texto_bloco,
                                          alvos=numeros_citados(texto_bloco)))
                if k == 1:
                    usados.add(i + 1)
                break

    # Etiqueta/desenho em volta do texto do rodapé (ex.: o quadrinho escuro com o
    # número da página): também é rodapé.
    texto_rodape = [r + (-2, -2, 2, 2) for r in linhas_rodape]

    def eh_rodape(r):
        if any(c.contains(r) for c in figs_cabecalho):
            return True
        if fundo_cabecalho and (r.y1 <= fundo_cabecalho + 6 or (r.y0 < fundo_cabecalho and r.y1 < altura * 0.1)):   # logotipo/linha/faixa do cabeçalho
            return True
        if r.y0 > topo_rodape or any(r.intersects(t) for t in texto_rodape):
            linhas_rodape.append(pymupdf.Rect(r))
            return True
        return False

    # --- Imagens ---
    for info in pagina.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        # Aba/faixa decorativa em imagem que sai pela borda da folha (ex.: ENEM 2024): não é conteúdo.
        if ((r.x0 < -1 or r.y0 < -1 or r.x1 > largura_pag + 1 or r.y1 > altura + 1)
                and (r.width < 60 or r.height < 60)):
            escondidos.append(r)
            continue
        # Fio decorativo entre as questões feito como imagem fininha (ex.: Fuvest 2027,
        # a linha colorida com bolinhas acima do número): não é conteúdo.
        if r.height < 8 and r.width > 100:
            escondidos.append(r)
            continue
        if not eh_rodape(r):
            itens.append(Item(pymupdf.Rect(info["bbox"]), "imagem"))

    # --- Desenhos vetoriais (gráficos, tabelas, molduras...) ---
    for r in desenhos:
        if any(r is d for d in divisorias):
            continue
        # Desenhos que pertencem a um selo/cabeçalho já estão na barreira.
        if any(b.tipo != "instrucao" and b.rect.contains(r) for b in barreiras):
            continue
        if eh_rodape(r):   # etiqueta do número da página etc.
            continue
        itens.append(Item(pymupdf.Rect(r), "desenho"))

    # O que fica totalmente fora da folha (sobras de diagramação) não é conteúdo.
    folha = pagina.rect
    itens = [it for it in itens if it.rect.intersects(folha)]

    # Rótulo "Questão NN" escondido DENTRO de uma faixa "Leia o texto..." (ex.: Fatec
    # 2024: um "Questão 14" esquecido embaixo da faixa, invisível na prova impressa).
    if modo == "rotulo":
        faixas = [b.rect for b in barreiras if b.tipo == "instrucao"]
        barreiras = [b for b in barreiras if not (
            b.tipo == "selo" and any(f.contains((b.rect.tl + b.rect.br) / 2) for f in faixas))]

    # Conteúdo que começa logo acima do rodapé e desce ao lado dele (ex.: Fatec, a
    # alternativa "(E)" no canto esquerdo, na altura de "VESTIBULAR ∙ Fatec 21"):
    # a área útil da página vai até o fim desse conteúdo.
    fundo_util = max([topo_rodape] + [it.rect.y1 + 1 for it in itens if it.rect.y0 < topo_rodape])

    # Modelos "numero" e "rotulo" em 2 colunas sem linha no meio (ex.: Fuvest, Unicamp): nenhuma linha de texto
    # atravessa o meio da folha e há texto dos dois lados. O texto passa do pé da coluna
    # esquerda para o alto da direita. Devolve o x do vão entre as colunas (ou None).
    vao_colunas = None
    if modo in ("numero", "rotulo"):
        textos = [it.rect for it in itens if it.tipo == "texto"]
        esq = [r for r in textos if r.x1 < meio + 5]
        dir_ = [r for r in textos if r.x0 > meio - 5]
        cruzam = [r for r in textos if r.x0 < meio - 5 and r.x1 > meio + 5]
        if len(esq) >= 5 and len(dir_) >= 5 and not cruzam:
            vao_colunas = (max(r.x1 for r in esq) + min(r.x0 for r in dir_)) / 2

    tem_selo = any(b.tipo == "selo" for b in barreiras)
    linhas_rodape += escondidos
    return {"barreiras": barreiras, "itens": itens, "divisorias": divisorias, "vao_colunas": vao_colunas,
            "topo_rodape": fundo_util, "linhas_rodape": linhas_rodape, "marcas_borda": marcas_borda,
            "so_folha_respostas": not tem_selo and bool(PADRAO_FOLHA_RESPOSTAS.search(pagina.get_text()))}


def linha_justa(linha):
    """Retângulo da linha de texto. Alguns PDFs informam uma caixa exageradamente alta
    (ex.: linha com expoente "10¹³"), que invadiria a questão de baixo; nesse caso a
    altura é recalculada pela posição e pelo tamanho da fonte de cada trecho."""
    rect = pymupdf.Rect(linha["bbox"])
    tamanho = max((s["size"] for s in linha["spans"] if s["text"].strip()), default=0)
    if tamanho and rect.height > 2 * tamanho:
        justo = None
        for s in linha["spans"]:
            if not s["text"].strip():
                continue
            x0, _, x1, _ = s["bbox"]
            base = s["origin"][1]
            r = pymupdf.Rect(x0, base - s["size"], x1, base + s["size"] * 0.3)
            justo = r if justo is None else justo | r
        if justo is not None:
            return justo
    return rect


def juntar_desenhos_que_tocam(rect, desenhos):
    """Une o retângulo do texto com os desenhos (fundo azul, barra colorida...)
    que estão atrás dele, para tratar selo/cabeçalho como uma peça só."""
    resultado = pymupdf.Rect(rect)
    for d in desenhos:
        if d.width < 3 and d.height > 30:   # linha divisória de colunas (ou um pedaço dela)
            continue
        if d.intersects(rect) and d.height < 40:
            resultado |= d   # "|=" une dois retângulos
    return resultado


def coluna_de(rect, meio):
    """0 = coluna esquerda, 1 = coluna direita (pelo centro do retângulo)."""
    centro_x = (rect.x0 + rect.x1) / 2
    return 0 if centro_x < meio else 1


# =====================================================================
# ETAPA 2: percorrer as colunas na ordem de leitura e montar as questões
# =====================================================================

def montar_questoes(doc):
    # Primeiro tenta o modelo com selos (2 colunas). Se o PDF não tiver nenhum selo,
    # tenta o modelo "Questão 01" (1 coluna).
    rodape = textos_de_rodape(doc)
    cabecalho = textos_de_cabecalho(doc)
    paginas = [analisar_pagina(p, "selo", rodape) for p in doc]
    uma_coluna = False
    modelo_numero = False
    if not any(b.tipo == "selo" for p in paginas for b in p["barreiras"]):
        paginas = [analisar_pagina(p, "rotulo", rodape, cabecalho) for p in doc]
        uma_coluna = True
    if not any(b.tipo == "selo" for p in paginas for b in p["barreiras"]):
        # 3º modelo: "6. Considere..." (número em negrito no começo da linha).
        uma_coluna = modelo_numero = True
        # Número em negrito; se assim quase nada for achado, aceita também o número sem
        # negrito (ex.: Cotuca, "7. As alternativas abaixo...") e fica com o que achar mais.
        melhor = None
        for sem_negrito in (False, True):
            tentativa = [analisar_pagina(p, "numero", rodape, cabecalho, sem_negrito) for p in doc]
            # Numeração que recomeça em cada matéria, com a matéria no código (ONC 2020:
            # "AF1..AF4" Física, "AQ1..AQ4" Química...): numera em sequência, na ordem da prova.
            selos = [b for p in tentativa for b in sorted(p["barreiras"], key=lambda b: b.rect.y0)
                     if b.tipo == "selo"]
            if (len(selos) >= 5 and all(len(b.prefixo) == 2 for b in selos)
                    and len({(b.prefixo, b.numero) for b in selos}) == len(selos)
                    and len({b.numero for b in selos}) < len(selos)):
                for i, b in enumerate(selos, start=1):
                    b.numero = i
            # Só vale uma numeração que segue em frente: sem repetir e sem pular muito
            # (um "1." em negrito numa lista dentro do texto não vira questão; vira texto comum).
            vistos, maior = set(), 0
            for p in tentativa:
                manter = []
                for b in sorted(p["barreiras"], key=lambda b: b.numero or 0):
                    if b.tipo == "selo" and (b.numero in vistos or b.numero > maior + 6):
                        continue
                    if b.tipo == "selo":
                        vistos.add(b.numero)
                        maior = max(maior, b.numero)
                    manter.append(b)
                p["barreiras"] = manter
            if melhor is None or len(vistos) > melhor[0] + 5:
                melhor = (len(vistos), tentativa)
            if len(vistos) >= 10:
                break
        paginas = melhor[1]
    questoes, apoios, blocos_soltos, avisos_gerais = [], [], [], []
    # Parte discursiva: as questões depois do título dela ficam fora do simulado (de vez; a redação
    # do ENEM, no meio do caderno, é diferente: lá as questões objetivas continuam depois).
    inicio_discursivas = None
    for num_pag, dados in enumerate(paginas):
        if inicio_discursivas is None:
            fim_d = next((b for b in sorted(dados["barreiras"], key=lambda b: b.rect.y0)
                          if b.tipo == "fim" and (b.texto or "").startswith("discursivas: ")), None)
            if fim_d:
                inicio_discursivas = (num_pag, fim_d.rect.y0)
        if inicio_discursivas is not None:
            fora = [b for b in dados["barreiras"] if b.tipo == "selo"
                    and (num_pag > inicio_discursivas[0] or b.rect.y0 > inicio_discursivas[1])]
            if fora:
                avisos_gerais.append(f"Página {num_pag + 1}: questões discursivas "
                                     f"{', '.join(str(b.numero) for b in fora)} ficaram fora do simulado.")
            dados["barreiras"] = [b for b in dados["barreiras"] if b not in fora]
    # Página de uma língua estrangeira (ex.: UERJ, "Linguagens  Espanhol" no alto da folha):
    # as questões dessa parte se repetem em cada língua; vale só a primeira versão.
    linguas = []
    for pg in doc:
        # o nome da língua sozinho numa linha do alto da folha, como título ("ALEMÃO", UFPR;
        # "Linguagens  Espanhol", UERJ); uma questão que cita "alemão" no texto não conta
        m_l = None
        # (linha por linha: o texto recortado da faixa pode colar letras de baixo, "Francêsfi")
        linhas_topo = ["".join(sp["text"] for sp in l["spans"]) for b in texto_dict(pg)["blocks"]
                       for l in b.get("lines", []) if l["bbox"][3] < pg.rect.height * 0.08]
        for linha_t in linhas_topo:
            m_l = m_l or re.fullmatch(r"\s*(?:linguagens\s+)?(espanhol|ingl[êe]s|franc[êe]s|alem[ãa]o|italiano)\s*",
                                      linha_t, re.I)
        linguas.append(m_l.group(1).lower() if m_l else None)
    pagina_idioma = [l is not None for l in linguas]

    # Em qual página aparece o primeiro selo? Tudo antes dele (capa, título,
    # cartão-resposta) é "preâmbulo" e não pertence a nenhuma questão.
    pagina_primeira = next(
        (i for i, p in enumerate(paginas) if any(b.tipo == "selo" for b in p["barreiras"])),
        None,
    )
    if pagina_primeira is None:
        return [], [], ["Nenhum selo de questão foi encontrado no PDF."]

    dono_atual = None    # quem recebe o conteúdo: Questao, Apoio, BlocoSolto ou None
    apoios_sem_alvo = []  # apoios "para responder à questão." sem número: valem para a próxima
    assunto_atual = ""    # matéria do último cabeçalho visto ("Língua Portuguesa"...)
    encerrado = False     # chegou na redação/rascunho: ignora até aparecer uma questão nova
    apoio_secao = None    # modelo "numero": texto logo abaixo do título da matéria
    apoio_aberto = None   # "Leia o texto abaixo.": vale para as próximas questões, até outro texto
    abertos = set()       # números de questão já vistos
    repetidas = []        # ENEM: questões 01 a 05 de novo, na opção espanhol
    secao_texto = ""      # texto do último título de seção
    # Língua estrangeira: a parte de cada língua repete os mesmos números; vale só a 1ª língua.
    # A língua vem do alto da folha (UERJ) ou de um título "ESPANHOL" no meio dela (UFPR).
    lingua = {"atual": None, "primeira": None}
    NOME_LINGUA = re.compile(r"\s*(?:linguagens\s+)?(espanhol|ingl[êe]s|franc[êe]s|alem[ãa]o|italiano)\s*", re.I)

    def outra_lingua():
        return lingua["atual"] is not None and lingua["atual"] != lingua["primeira"]

    def abrir(b, num_pag):
        """Uma barreira começa algo novo: muda o "dono" do conteúdo que vem depois dela."""
        nonlocal dono_atual, apoios_sem_alvo, assunto_atual, apoio_secao, secao_texto, apoio_aberto
        if b.tipo == "cabecalho" and NOME_LINGUA.fullmatch(b.texto or ""):
            lingua["atual"] = NOME_LINGUA.fullmatch(b.texto).group(1).lower()
            lingua["primeira"] = lingua["primeira"] or lingua["atual"]
        if b.tipo != "selo":
            apoio_aberto = None
        if b.tipo in ("instrucao", "cabecalho") and outra_lingua():
            # título/texto/instrução da parte de OUTRA língua (ex.: UFPR, "ESPANHOL"): fica de fora
            apoio_secao = None
            dono_atual = BlocoSolto(num_pag, rascunho=True)
            blocos_soltos.append(dono_atual)
            return
        if b.tipo == "selo":
            # ENEM: "Questões de 01 a 05 (opção espanhol)" repete os números da opção inglês.
            # Fica só a primeira versão (o simulado tem um gabarito por número).
            if b.numero in abertos and (re.search(r"op[çc][ãa]o|espanhol|ingl[êe]s", secao_texto, re.I)
                                        or pagina_idioma[num_pag] or outra_lingua()):
                repetidas.append(b.numero)
                dono_atual = BlocoSolto(num_pag, rascunho=True)
                blocos_soltos.append(dono_atual)
                return
            if b.numero > max(abertos, default=0):
                secao_texto = ""
            abertos.add(b.numero)
            dono_atual = Questao(b.numero, num_pag, b.rect, assunto=assunto_atual)
            questoes.append(dono_atual)
            if apoio_secao is not None:
                apoio_secao.alvos.append(b.numero)
            if apoio_aberto is not None:
                apoio_aberto.alvos.append(b.numero)
            for a in apoios:
                if a.proxima is None:
                    a.proxima = b.numero
            for a in apoios_sem_alvo:
                a.alvos = [b.numero]
            apoios_sem_alvo = []
        elif b.tipo == "instrucao":
            if instrucao_dentro_da_questao(b, dono_atual, num_pag):
                return   # é só uma frase do enunciado; segue com a mesma questão
            dono_atual = Apoio(num_pag, b.texto, alvos=b.alvos)
            apoios.append(dono_atual)
            if b.alvos is None and PADRAO_LEIA_ABAIXO.match(b.texto):
                dono_atual.alvos = []
                apoio_aberto = dono_atual
            elif b.alvos is None:
                apoios_sem_alvo.append(dono_atual)
        elif b.tipo == "fim":
            dono_atual = None
        elif b.tipo == "rascunho":   # coluna de rascunho: nada ali é de questão
            dono_atual = BlocoSolto(num_pag, rascunho=True)
            blocos_soltos.append(dono_atual)
        elif re.fullmatch(r"(problemas|quest[õo]es)\s+de\s+\d\s+pontos", b.texto, re.IGNORECASE):
            # Canguru: "Problemas de 4 pontos" só divide a prova; não é matéria nem texto de apoio.
            dono_atual = BlocoSolto(num_pag)
            blocos_soltos.append(dono_atual)
            apoio_secao = None
        elif uma_coluna:
            # Título da matéria: o texto logo abaixo dele (sem "Leia o texto...") vale
            # para todas as questões da matéria, até o próximo título.
            secao_texto = b.texto
            if not re.match(r"quest", b.texto, re.I):   # "Questões de 01 a 05" não é matéria
                assunto_atual = nome_do_assunto(b.texto)
            apoio_secao = dono_atual = Apoio(num_pag, b.texto, alvos=[])
            apoios.append(dono_atual)
        else:  # cabeçalho de seção: não entra em imagem nenhuma
            assunto_atual = nome_do_assunto(b.texto)
            dono_atual = BlocoSolto(num_pag)
            blocos_soltos.append(dono_atual)

    for num_pag, dados in enumerate(paginas):
        retomada = False
        # Parte de língua estrangeira (ex.: UERJ): a folha que abre a parte de uma língua começa com
        # o texto dela, que vale para as questões dessa língua. A 1ª língua fica; as outras, que
        # repetem os mesmos números, ficam de fora.
        anterior_lingua = linguas[num_pag - 1] if num_pag > 0 else None
        if linguas[num_pag]:
            lingua["atual"] = linguas[num_pag]
            lingua["primeira"] = lingua["primeira"] or linguas[num_pag]
        elif anterior_lingua:
            lingua["atual"] = None   # acabou a parte das línguas (UERJ: começa Matemática)
        if linguas[num_pag] and linguas[num_pag] != anterior_lingua:
            if not any(linguas[:num_pag]):
                dono_atual = apoio_aberto = Apoio(num_pag, "Texto em " + linguas[num_pag], alvos=[])
                apoios.append(dono_atual)
            else:
                apoio_aberto = None
                dono_atual = BlocoSolto(num_pag, rascunho=True)
                blocos_soltos.append(dono_atual)
        elif anterior_lingua and not linguas[num_pag]:
            apoio_aberto = None   # acabou a parte das línguas
        if encerrado:
            # ENEM: a redação fica no meio do caderno; as questões continuam depois dela.
            if not any(b.tipo == "selo" and b.numero > max(abertos, default=0) for b in dados["barreiras"]):
                avisos_gerais.append(f"Página {num_pag + 1} ignorada (redação/rascunho).")
                continue
            encerrado, retomada, dono_atual = False, True, None
        if num_pag < pagina_primeira:
            avisos_gerais.append(f"Página {num_pag + 1} ignorada (antes da primeira questão).")
            continue
        if dados["so_folha_respostas"]:
            avisos_gerais.append(f"Página {num_pag + 1} ignorada (folha de respostas).")
            dono_atual = None   # nada desta página continua a última questão
            continue
        # Página sem barreira e quase sem texto (contracapa com logotipo e código de barras):
        # não continua a última questão.
        letras = sum(len(re.findall(r"[A-Za-zÀ-ÿ]", it.texto or "")) for it in dados["itens"] if it.tipo == "texto")
        if not dados["barreiras"] and letras < 15 and not any(it.tipo == "imagem" for it in dados["itens"]):
            dono_atual = None
        if dono_atual is None and not dados["barreiras"]:
            avisos_gerais.append(f"Página {num_pag + 1} ignorada (sem questões; ex.: contracapa).")
            continue

        largura = doc[num_pag].rect.width
        # Em provas de 1 coluna, "meio" fica além da página: tudo cai na coluna 0.
        meio = largura * 10 if uma_coluna else largura / 2
        fundo = dados["topo_rodape"] - 1

        # Na página da primeira questão, ignoramos tudo acima da primeira
        # barreira (título da prova e cartão-resposta com os números 01..40).
        topo = 0
        if num_pag == pagina_primeira or retomada:
            topo = min(b.rect.y0 for b in dados["barreiras"])
            # O que fica acima (capa, cartão-resposta, ilustração de abertura) não é de
            # nenhuma questão: sai da lista para não gerar aviso de "mistura" à toa.
            dados["itens"] = [it for it in dados["itens"]
                              if (it.rect.y0 + it.rect.y1) / 2 >= topo or it.tipo == "selo"]

        # Modelo "Questão 01": o que começa ACIMA do rótulo e fica ao lado dele (ex.: Fatec,
        # a fonte "Acesso em: 30.09.2024" de uma figura, à direita de "Questão 56") é do
        # conteúdo de cima, mesmo que o centro fique abaixo do topo do rótulo.
        if uma_coluna:
            for b in dados["barreiras"]:
                if b.tipo != "selo":
                    continue
                for it in dados["itens"]:
                    # (mas não a 1ª linha de um parágrafo que continua logo abaixo: ex.: UERJ, o texto
                    # ao lado do "05" enorme começa 1 ponto acima dele e é da questão 5)
                    continua = any(o is not it and o.tipo == "texto" and abs(o.rect.x0 - it.rect.x0) < 3
                                   and -4 < o.rect.y0 - it.rect.y1 < 3 for o in dados["itens"])
                    # (nem um pedaço alto da 1ª linha da questão, ex.: UERJ, a fração "(1/8)" no fim de
                    # "Observe os gráficos... g(x) = (1/8)^x-1": fica na altura dessa linha)
                    # (nem um título sozinho no alto da folha, sem nada logo acima dele: ex.: UERJ,
                    # "NÍVEL DE SATISFAÇÃO..." ao lado de "Questão 47" é da 47, não da 46 da folha anterior)
                    continua = continua or not any(
                        o is not it and o.rect.y0 < it.rect.y0 - 1 and it.rect.y0 - o.rect.y1 < 30
                        and min(o.rect.x1, it.rect.x1) - max(o.rect.x0, it.rect.x0) > 0
                        for o in dados["itens"])
                    continua = continua or any(
                        o is not it and o.tipo == "texto" and o.rect.x0 >= b.rect.x1 - 2
                        and b.rect.y0 - 3 <= o.rect.y0 <= b.rect.y0 + 10 and o.rect.x1 < it.rect.x0 + 5
                        and min(o.rect.y1, it.rect.y1) - max(o.rect.y0, it.rect.y0) > 0.5 * o.rect.height
                        for o in dados["itens"])
                    if not modelo_numero and not continua and it.rect.y0 < b.rect.y0 - 0.5 and it.rect.x0 >= b.rect.x1 + 20                             and b.rect.y0 <= it.centro_y() < b.rect.y1:
                        it.ancora_y = b.rect.y0 - 0.5
                    # Modelo "numero": fórmula alta NA linha da questão ("23. Calcule ... E = 12(⅓+⅚)"),
                    # que sobe um pouco acima dela: é da própria questão.
                    elif (modelo_numero and it.rect.y0 < b.rect.y0 and it.rect.y1 > b.rect.y0 + 2
                          and b.rect.x0 + 20 < it.rect.x0 and it.rect.height < 40):
                        it.ancora_y = b.rect.y0 + 0.5

        # Prova de 1 coluna, mas com questões LADO A LADO nesta página (ex.: Fatec,
        # questões 41 e 42 uma ao lado da outra): cada questão ganha a sua área.
        # Modelo "Questão 01" com uma linha alta entre as colunas (ex.: Fatec 2020): a página tem
        # 2 colunas e o texto passa do pé da esquerda para o alto da direita.
        # (tem que ficar no meio da folha: as laterais de uma moldura em volta das
        # alternativas também são linhas altas, mas não separam colunas)
        divisoria_alta = [d for d in dados["divisorias"] if d.height > doc[num_pag].rect.height * 0.5
                          and abs((d.x0 + d.x1) / 2 - largura / 2) < largura * 0.06]
        # (no modelo "6. Considere..." também, ex.: UFRGS, OBQ: o texto do pé da coluna esquerda
        # continua do lado da questão que começa na direita, e não há vão limpo entre as colunas)
        # (mas não se algum texto atravessa a linha: ex.: UFRGS, questão de largura inteira no alto
        # da folha e as duas colunas só embaixo dela)
        if modelo_numero and divisoria_alta:
            m_div = (divisoria_alta[0].x0 + divisoria_alta[0].x1) / 2
            # (o cabeçalho no alto da folha, acima da linha, não conta: ex.: OBQ, "OBQ 2025")
            if any(it.tipo == "texto" and it.rect.x0 < m_div - 5 and it.rect.x1 > m_div + 5
                   and not (it.rect.y1 <= divisoria_alta[0].y0 and it.rect.y1 < doc[num_pag].rect.height * 0.1)
                   for it in dados["itens"]):
                divisoria_alta = []
        duas_colunas = not uma_coluna or bool(divisoria_alta)
        if duas_colunas and uma_coluna:
            meio = (divisoria_alta[0].x0 + divisoria_alta[0].x1) / 2
        elif dados["vao_colunas"]:
            # Com a linha alta no meio da folha, ela é a divisa (ex.: OBMEP; um texto da coluna
            # direita que começa mais à esquerda puxaria o vão para dentro da coluna esquerda).
            duas_colunas = True
            meio = (divisoria_alta[0].x0 + divisoria_alta[0].x1) / 2 if divisoria_alta else dados["vao_colunas"]

        if not duas_colunas and (modelo_numero or uma_coluna and any(
                b.tipo == "selo" and b.rect.x0 > largura * 0.3 for b in dados["barreiras"])):
            encerrado = montar_pagina_lado_a_lado(num_pag, dados, largura, topo, fundo, abrir,
                                                  lambda: dono_atual, avisos_gerais)
            continue

        for coluna in ((0, 1) if duas_colunas else (0,)):
            x0 = 0 if coluna == 0 else meio
            x1 = (meio if coluna == 0 else largura) if duas_colunas else largura
            if duas_colunas and uma_coluna:   # a linha pontilhada entre as colunas fica de fora
                x0, x1 = (x0, x1 - 2) if coluna == 0 else (x0 + 2, x1)

            barreiras = sorted(
                (b for b in dados["barreiras"]
                 if coluna_de(b.rect, meio) == coluna and b.rect.y0 >= topo),
                key=lambda b: b.rect.y0,
            )

            cursor = topo
            for b in barreiras:
                # A região entre o cursor e esta barreira pertence ao dono atual.
                adicionar_regiao(num_pag, dados, coluna, meio, x0, x1, cursor, b.rect.y0,
                                 dono_atual, avisos_gerais)
                cursor = b.rect.y0
                abrir(b, num_pag)
                if b.tipo == "cabecalho":
                    cursor = b.rect.y1
                if b.tipo == "fim":
                    encerrado = True
                    break

            # Da última barreira até o fim da coluna.
            if not encerrado:
                adicionar_regiao(num_pag, dados, coluna, meio, x0, x1, cursor, fundo,
                                 dono_atual, avisos_gerais)

    # No modelo "Questão 01", os arquivos seguem o número da questão (em páginas com
    # questões lado a lado, a ordem de leitura sozinha poderia trocar 53 com 54).
    if uma_coluna:
        questoes.sort(key=lambda q: q.numero_selo)

    # Recorta cada trecho justo ao redor do conteúdo.
    for q in questoes:
        q.trechos = finalizar_trechos(doc, paginas, q.trechos, q.avisos)
    for a in apoios:
        avisos_apoio = []
        a.trechos = finalizar_trechos(doc, paginas, a.trechos, avisos_apoio)
        for pag, msg in avisos_apoio:
            avisos_gerais.append(f"Página {pag + 1}: texto de apoio ({a.texto[:50]}...): {msg}. REVISAR.")

    # "Texto em alemão" que ficou sem questão (a parte da língua já tinha a instrução dela, ex.: UFPR)
    apoios[:] = [a for a in apoios if not (a.texto.startswith("Texto em ") and not a.alvos)]
    ligar_apoios(questoes, apoios, avisos_gerais)
    if repetidas:
        avisos_gerais.append(
            f"As questões {repetidas[0]:02d} a {repetidas[-1]:02d} aparecem duas vezes (opção de língua "
            f"estrangeira, ex.: inglês e espanhol). Usei só a primeira versão.")

    for bloco in blocos_soltos:
        if bloco.trechos and not bloco.rascunho:
            avisos_gerais.append(
                f"Página {bloco.pagina + 1}: há conteúdo logo após um cabeçalho de seção que não "
                f"pertence a nenhuma questão (não foi usado). REVISAR."
            )
    return questoes, apoios, avisos_gerais


def montar_pagina_lado_a_lado(num_pag, dados, largura, topo, fundo, abrir, dono, avisos_gerais):
    """Página em que algumas questões ficam lado a lado (ou ao lado de uma figura).
    Cada barreira (questão, "Leia o texto..." etc.) ganha uma área retangular:
      - em cima: a própria barreira;
      - embaixo: a próxima barreira que começa na mesma coluna ou mais à esquerda;
      - à direita: a barreira que fica ao lado dela, na mesma altura;
      - à esquerda: a borda da página, ou a coluna ao lado, se houver.
    Cada elemento da página vai para a área que contém o seu centro.
    Devolve True se a página tiver o fim das questões (redação)."""
    barreiras = sorted((b for b in dados["barreiras"] if b.rect.y0 >= topo),
                       key=lambda b: (round(b.rect.y0 / 8), b.rect.x0))
    if not barreiras:
        return False
    # Título de matéria centralizado ("FÍSICA", ex.: UFPR) ocupa a largura toda: não é uma
    # coluna ao lado dos textos de baixo (senão "O texto a seguir é referência..." fica espremido).
    esquerda = min(b.rect.x0 for b in barreiras)
    for b in barreiras:
        # (o mesmo para "Leia o texto para responder às questões 18 e 19." centralizada, ex.: Etec 2012)
        if b.tipo in ("cabecalho", "instrucao") and abs((b.rect.x0 + b.rect.x1) / 2 - largura / 2) < largura * 0.1:
            b.rect = pymupdf.Rect(esquerda, b.rect.y0, max(b.rect.x1, largura - esquerda), b.rect.y1)
    # Questão que começa mais à direita só porque tem uma foto à esquerda (texto contornando a
    # figura, ex.: ONC 2018): à esquerda dela, na mesma altura, não há texto nenhum, só figura.
    # Ela não está "ao lado" de outra questão; vale como se começasse na margem.
    margem = min(b.rect.x0 for b in barreiras)
    for b in barreiras:
        if b.tipo != "selo" or b.rect.x0 < largura * 0.3:
            continue
        # Se a barreira logo acima é um texto de apoio ("Leia a tirinha..."), a figura é dele
        # e a questão está mesmo ao lado (ex.: Fatec): não mexe.
        acima = [o for o in barreiras if o.rect.y0 < b.rect.y0 - 3]
        if acima and max(acima, key=lambda o: o.rect.y0).tipo != "selo":
            continue
        faixa = pymupdf.Rect(0, b.rect.y0, b.rect.x0 - 5, b.rect.y0 + 40)
        ao_lado_esq = [it for it in dados["itens"] if it.rect.intersects(faixa)]
        # (a foto começa junto com a questão; uma figura que vem lá de cima é de outra coisa)
        if ao_lado_esq and all(it.tipo != "texto" and it.rect.y0 >= b.rect.y0 - 15
                               for it in ao_lado_esq) and not any(
                o is not b and o.tipo == "selo" and o.rect.x0 < b.rect.x0 - 40
                and abs(o.rect.y0 - b.rect.y0) < 40 for o in barreiras):
            b.rect = pymupdf.Rect(margem, b.rect.y0, b.rect.x1, b.rect.y1)
    # A barreira b "fecha" a área de a por baixo se começar na coluna de a (ou à esquerda)
    # e se estender até a coluna de a (ex.: a questão 52 da coluna esquerda não fecha a 54,
    # que está na direita).
    fim_coluna = lambda b: b.coluna_x1 or b.rect.x1
    embaixo = {}
    for a in barreiras:
        abaixo = [b.rect.y0 for b in barreiras
                  if b is not a and b.rect.y0 > a.rect.y0 + 3 and b.rect.x0 <= a.rect.x0 + 40
                  and fim_coluna(b) >= a.rect.x0 + 40]
        embaixo[id(a)] = min(abaixo, default=fundo)

    dono_anterior = dono()   # o que vem antes da 1ª barreira continua a página anterior
    regioes, encerrado = [], False
    def sobrepoe(a, b, y1):   # as faixas verticais de a e b se cruzam?
        return b.rect.y0 < y1 - 5 and embaixo[id(b)] > a.rect.y0 + 5

    for a in barreiras:
        y1 = embaixo[id(a)]
        ao_lado = [b for b in barreiras if b.rect.x0 > a.rect.x0 + 40 and sobrepoe(a, b, y1)]
        # Linha vertical entre as colunas, na altura desta área: ela é o limite.
        # (Só vale linha no meio da folha ou que acompanhe boa parte da área: o eixo desenhado
        # num globo, ex.: OBA, é uma linha alta mas não separa nada.)
        linhas = [(d.x0 + d.x1) / 2 for d in dados["divisorias"]
                  if d.y0 < y1 - 5 and d.y1 > a.rect.y0 + 5
                  and (abs((d.x0 + d.x1) / 2 - largura / 2) < largura * 0.1
                       or min(d.y1, y1) - max(d.y0, a.rect.y0) > 0.5 * (y1 - a.rect.y0))]
        limite_dir = min([x - 2 for x in linhas if x > a.rect.x0 + 40], default=largura)
        x1 = min(min(b.rect.x0 for b in ao_lado) - 4 if ao_lado else largura, limite_dir)
        a_esquerda = any(b.rect.x0 < a.rect.x0 - 40 and sobrepoe(a, b, y1) for b in barreiras)
        x0 = a.rect.x0 - 6 if a_esquerda else 0
        x0 = max([x0] + [x + 2 for x in linhas if x < a.rect.x0])
        partes = [("corpo", pymupdf.Rect(x0, a.rect.y0, x1, y1))]
        # O vizinho da direita só começa mais abaixo: até lá, esta área usa a largura toda
        # (ex.: um parágrafo de ponta a ponta e, depois, a figura de um lado e a questão do outro).
        if ao_lado:
            topo_vizinho = min(b.rect.y0 for b in ao_lado)
            if topo_vizinho > a.rect.y1 + 3:
                partes = [("topo", pymupdf.Rect(x0, a.rect.y0, limite_dir, topo_vizinho)),
                          ("corpo", pymupdf.Rect(x0, topo_vizinho, x1, y1))]
        # "Leia o texto para responder à questão." logo abaixo de "QUESTÃO 23", na mesma
        # coluna: é parte da 23 (com questões lado a lado, a última aberta pode ser a 24).
        acima = [(b, d) for b, _, d in regioes
                 if b.tipo == "selo" and isinstance(d, Questao) and abs(b.rect.x0 - a.rect.x0) < 40
                 and (0 <= a.rect.y0 - b.rect.y1 < DISTANCIA_INSTRUCAO_DENTRO
                      or a.alvos and b.numero in a.alvos)]
        if a.tipo == "instrucao" and acima:
            regioes.append((a, partes, acima[-1][1]))
            continue
        abrir(a, num_pag)
        novo = dono()
        if isinstance(novo, Apoio) and novo.alvos:
            novo.proxima = novo.alvos[0]   # nesta página a ordem de leitura não diz qual vem depois
        regioes.append((a, partes, novo))
        if a.tipo == "fim":
            encerrado = True
            break

    primeiro_topo = barreiras[0].rect.y0
    por_dono = {}
    for it in dados["itens"]:
        cx, cy = (it.rect.x0 + it.rect.x1) / 2, it.centro_y()
        # a própria barreira (o "Questão 01", a frase "Leia o texto...") é do seu dono
        alvo, regiao, parte = None, None, "corpo"
        for b, ps, d in regioes:
            if (b.rect + (-1, -1, 1, 1)).contains(it.rect):
                if b.tipo == "cabecalho":   # o próprio título da matéria não entra na imagem
                    alvo = False
                    break
                alvo, (parte, regiao) = d, ps[0]
                # Título mais largo que a área (ex.: "Leia o quadrinho..." de ponta a ponta,
                # com o quadrinho só à esquerda): vira um pedaço separado, empilhado em cima.
                if b.rect.x1 > regiao.x1 + 10 or b.rect.x0 < regiao.x0 - 10:
                    regiao, parte = pymupdf.Rect(b.rect), "titulo"
                break
        if alvo is False:
            continue
        if alvo is None:
            todas = [(p, r, d) for b, ps, d in regioes for p, r in ps]
            dentro = [t for t in todas if t[1].x0 <= cx <= t[1].x1 and t[1].y0 <= cy < t[1].y1]
            if not dentro:   # sobrou: vai para a área mais próxima acima, na mesma faixa
                dentro = [t for t in todas if t[1].x0 <= cx <= t[1].x1 and t[1].y0 <= cy]
            if dentro:
                parte, regiao, alvo = max(dentro, key=lambda t: t[1].y0)
            elif cy < primeiro_topo:
                alvo = dono_anterior
                regiao = pymupdf.Rect(0, topo, largura, primeiro_topo)
        if alvo is None:
            if encerrado and cy >= regioes[-1][0].rect.y0:
                continue   # conteúdo da redação
            avisos_gerais.append(f"Página {num_pag + 1}: um elemento ({_fmt(it.rect)}) não pertence "
                                 f"a nenhuma questão (não foi usado). REVISAR.")
            continue
        chave = (id(alvo), parte)
        if chave not in por_dono:
            por_dono[chave] = [alvo, pymupdf.Rect(regiao), []]
        por_dono[chave][1] |= regiao
        por_dono[chave][2].append(it)

    # Na imagem: título (se separado), depois a faixa larga de cima, depois o resto.
    ordem = {"titulo": 0, "topo": 1, "corpo": 2}
    for (_, parte), (alvo, rect, itens) in sorted(por_dono.items(), key=lambda kv: ordem[kv[0][1]]):
        alvo.trechos.append(Trecho(num_pag, rect, itens))
    return encerrado


def nome_do_assunto(texto_cabecalho):
    """'BLOCO 1 · LÍNGUA PORTUGUESA' -> 'Língua Portuguesa'."""
    texto = re.sub(r"^\s*BLOCO\s+\d+\s*[·\-–:]\s*", "", texto_cabecalho, flags=re.IGNORECASE)
    pequenas = {"de", "da", "do", "das", "dos", "e", "a", "o", "suas", "seus"}
    palavras = texto.lower().split()
    return " ".join(p if (i > 0 and p in pequenas) else p.capitalize()
                    for i, p in enumerate(palavras))


def instrucao_dentro_da_questao(barreira, dono, num_pag):
    """Uma frase "Leia o texto..." logo abaixo do selo, ou que cita a própria
    questão em andamento, faz parte do enunciado (formato da 1ª prova testada)."""
    if not isinstance(dono, Questao):
        return False
    if barreira.alvos and dono.numero_selo in barreira.alvos:
        return True
    return (dono.pagina == num_pag
            and 0 <= barreira.rect.y0 - dono.rect_selo.y1 < DISTANCIA_INSTRUCAO_DENTRO)


def adicionar_regiao(num_pag, dados, coluna, meio, x0, x1, y0, y1, dono, avisos_gerais):
    """Coloca no "dono" os itens da coluna cujo centro está entre y0 e y1."""
    if y1 <= y0:
        return
    itens = [
        it for it in dados["itens"]
        if coluna_de(it.rect, meio) == coluna and y0 <= it.centro_y() < y1
    ]
    if not itens:
        return
    if dono is None:
        avisos_gerais.append(
            f"Página {num_pag + 1}: há conteúdo antes da primeira questão que não foi usado "
            f"(coluna {'esquerda' if coluna == 0 else 'direita'}). REVISAR."
        )
        return
    # Se o último trecho do dono termina exatamente onde esta região começa
    # (mesma página e coluna), emendamos; senão é um trecho novo (conteúdo
    # que continua em outra coluna/página).
    limites = pymupdf.Rect(x0, y0, x1, y1)
    ultimo = dono.trechos[-1] if dono.trechos else None
    if ultimo and ultimo.pagina == num_pag and ultimo.rect.x0 == x0 \
            and abs(ultimo.rect.y1 - y0) < 0.01:
        ultimo.rect |= limites
        ultimo.itens.extend(itens)
    else:
        dono.trechos.append(Trecho(num_pag, limites, itens))


def finalizar_trechos(doc, paginas, trechos, avisos):
    """Transforma cada região (faixa da coluna entre barreiras) num recorte
    justo ao redor do conteúdo, com margem, verificando se nada de fora
    (de outra questão) invade o recorte."""
    finais = []
    for t in trechos:
        # Pedaço que é só um fio solto (ex.: UFRGS, o fio no alto da folha seguinte, que deixava
        # a imagem da questão com a largura da folha inteira e metade em branco): fica de fora.
        if t.itens and all(it.tipo == "desenho" and min(it.rect.width, it.rect.height) < 3 for it in t.itens):
            fios = pymupdf.Rect(t.itens[0].rect)
            for it in t.itens[1:]:
                fios |= it.rect
            if fios.height < 15 and len(trechos) > 1:
                continue
        largura = doc[t.pagina].rect.width
        meio = largura / 2
        # Com uma linha divisória de verdade no meio da folha, um desenho que passa um pouco
        # dela (ex.: OBMEP, a sombra de uma figura) não puxa o recorte para a outra coluna.
        dividida = any(abs((d.x0 + d.x1) / 2 - meio) < 15 and d.height > doc[t.pagina].rect.height * 0.4
                       for d in paginas[t.pagina]["divisorias"])
        def ret(it):
            if dividida and it.tipo == "desenho" and not (it.rect & t.rect).is_empty:
                return it.rect & pymupdf.Rect(t.rect.x0, it.rect.y0, t.rect.x1, it.rect.y1)
            return it.rect
        conteudo = pymupdf.Rect(ret(t.itens[0]))
        for it in t.itens[1:]:
            conteudo |= ret(it)

        # Limite permitido: a faixa da coluna, ampliada para caber o próprio
        # conteúdo. Uma figura larga que ocupa as duas colunas (acontece em
        # algumas provas) libera a página inteira na horizontal.
        limite = pymupdf.Rect(t.rect) | conteudo
        if conteudo.x0 < meio - 5 and conteudo.x1 > meio + 5:
            limite.x0, limite.x1 = 0, largura

        recorte = (conteudo + (-MARGEM, -MARGEM, MARGEM, MARGEM)) & limite

        # Garante que a margem não "engula" pedaços de outros elementos da
        # página; se algo de fora estiver EM CIMA do conteúdo, avisa.
        dados = paginas[t.pagina]
        meus = {id(it) for it in t.itens}
        estranhos = [it.rect for it in dados["itens"] if id(it) not in meus]
        estranhos += [b.rect for b in dados["barreiras"]
                      if b.tipo == "cabecalho" or not any(b.rect == it.rect for it in t.itens)
                      and b.tipo == "selo"]
        estranhos += dados["divisorias"]
        tapar_estranhos = []
        for e in estranhos:
            inter = e & recorte
            if inter.is_empty or inter.width < 0.5 or inter.height < 0.5:
                continue
            if e.y1 <= conteudo.y0 + 0.5:
                recorte.y0 = max(recorte.y0, e.y1)
            elif e.y0 >= conteudo.y1 - 0.5:
                recorte.y1 = min(recorte.y1, e.y0)
            # Encostam de leve (até 6 pontos, cerca de 2 mm): corta no meio da sobreposição.
            elif e.y1 <= conteudo.y0 + 6 and e.y0 < conteudo.y0:
                recorte.y0 = max(recorte.y0, (e.y1 + conteudo.y0) / 2)
            elif e.y0 >= conteudo.y1 - 6 and e.y1 > conteudo.y1:
                recorte.y1 = min(recorte.y1, (e.y0 + conteudo.y1) / 2)
            elif e.x1 <= conteudo.x0 + 0.5:
                recorte.x0 = max(recorte.x0, e.x1)
            elif e.x0 >= conteudo.x1 - 0.5:
                recorte.x1 = min(recorte.x1, e.x0)
            elif not any(inter.intersects(it.rect) for it in t.itens) and e not in dados["divisorias"]:
                # Fica no recorte mas ao LADO do conteúdo (ex.: ponta de uma figura do texto de
                # apoio ao lado de "Questão 56"): some da imagem, sem cortar nada da questão.
                tapar_estranhos.append(pymupdf.Rect(inter))
            else:
                avisos.append((t.pagina, f"conteúdo de fora se sobrepõe ao recorte "
                                         f"({_fmt(e)}); possível mistura de questões"))
        # Rodapé que ficou dentro do recorte (ao lado da última linha): some da imagem.
        tapar = [r & recorte for r in dados.get("linhas_rodape", [])
                 if not (r & recorte).is_empty and not any(r.intersects(it.rect) for it in t.itens)]
        tapar += [r & recorte for r in tapar_estranhos + dados.get("marcas_borda", []) if not (r & recorte).is_empty]
        finais.append(Trecho(t.pagina, recorte, t.itens, tapar))
    return finais


def _fmt(r):
    return f"x {r.x0:.0f}-{r.x1:.0f}, y {r.y0:.0f}-{r.y1:.0f}"


# =====================================================================
# ETAPA 3: ligar os textos de apoio às questões
# =====================================================================

def ligar_apoios(questoes, apoios, avisos_gerais):
    por_numero = {q.numero_selo: q for q in questoes}
    for a in apoios:
        if not a.trechos:
            continue
        if not a.alvos:
            avisos_gerais.append(f"Página {a.pagina + 1}: texto de apoio sem questão depois dele "
                                 f"(\"{a.texto[:60]}\"). REVISAR.")
            continue
        for n in a.alvos:
            if n in por_numero:
                por_numero[n].apoios.append(a)
            else:
                avisos_gerais.append(f"Página {a.pagina + 1}: \"{a.texto[:60]}\" cita a questão "
                                     f"{n}, que não foi encontrada. REVISAR.")
        if a.proxima is not None and a.proxima not in a.alvos:
            avisos_gerais.append(
                f"Página {a.pagina + 1}: \"{a.texto[:60]}\" aparece logo antes da questão "
                f"{a.proxima}, que não é citada nele. REVISAR.")

    # Texto de apoio compartilhado "escondido" dentro de uma questão.
    for q in questoes:
        texto = " ".join(it.texto for t in q.trechos for it in t.itens if it.tipo == "texto")
        alvos = numeros_citados(texto) or []
        outros = [n for n in alvos if n != q.numero_selo]
        if outros:
            q.avisos.append((q.pagina, f"o texto da questão cita as questões {alvos}: confira se "
                                       f"o texto de apoio precisa ser repetido nelas"))


# =====================================================================
# ETAPA 4 (opcional): achar as alternativas pelos pixels
# =====================================================================
# Ideia: renderizamos o trecho em tons de cinza e dividimos em linhas de
# texto (faixas horizontais com tinta). Em cada linha, separamos os
# primeiros "glifos" (desenhos de letras) pelas colunas vazias entre eles.
# Uma linha de alternativa começa com "(", letra, ")". O parêntese tem uma
# assinatura bem clara: é estreito e desce ABAIXO da base da letra maiúscula
# ao lado (como a perninha do "p"). A letra entre eles é mais larga.

def renderizar_cinza(doc, trecho, dpi):
    return doc[trecho.pagina].get_pixmap(dpi=dpi, clip=trecho.rect, colorspace=pymupdf.csGRAY)


def linhas_de_texto(pix):
    """Lista de (y_inicio, y_fim) de cada faixa horizontal com tinta."""
    w, h, s = pix.width, pix.height, pix.samples
    # min() de uma linha de bytes é rápido: diz se há algum pixel escuro.
    tem_tinta = [min(s[y * w:(y + 1) * w]) < LIMIAR_ESCURO for y in range(h)]
    linhas, y = [], 0
    while y < h:
        if tem_tinta[y]:
            inicio = y
            while y < h and tem_tinta[y]:
                y += 1
            linhas.append((inicio, y))
        else:
            y += 1
    return linhas


def primeiros_glifos(pix, y0, y1, quantos=3):
    """Os primeiros glifos da linha, da esquerda para a direita.
    Cada glifo = (x_inicio, x_fim, y_topo, y_base)."""
    w, s = pix.width, pix.samples
    faixas = [s[y * w:(y + 1) * w] for y in range(y0, y1)]
    glifos, x = [], 0
    coluna_tem_tinta = lambda c: min(f[c] for f in faixas) < LIMIAR_ESCURO
    while x < w and len(glifos) < quantos:
        if coluna_tem_tinta(x):
            inicio = x
            while x < w and coluna_tem_tinta(x):
                x += 1
            ys = [i for i, f in enumerate(faixas) if min(f[inicio:x]) < LIMIAR_ESCURO]
            glifos.append((inicio, x, y0 + ys[0], y0 + ys[-1] + 1))
        else:
            x += 1
            if glifos and x - glifos[-1][1] > (y1 - y0):   # espaço grande = fim da palavra
                break
    return glifos


def parece_alternativa(glifos):
    """True se os glifos parecem "(", letra, ")"."""
    if len(glifos) < 3:
        return False
    abre, letra, fecha = glifos[:3]
    estreito = lambda g: (g[1] - g[0]) < 0.35 * (g[3] - g[2])
    return (
        estreito(abre) and estreito(fecha)
        # descem abaixo da letra (em algumas fontes, só 1 ou 2 pixels) e são mais altos que ela
        and abre[3] > letra[3] and fecha[3] > letra[3]
        and (abre[3] - abre[2]) > (letra[3] - letra[2])
        and abre[2] <= letra[2] + 1                               # e sobem até o topo dela
        and (letra[1] - letra[0]) > (abre[1] - abre[0])           # letra é mais larga
    )


def achar_corte_alternativas(doc, trecho):
    """Procura o grupo final de alternativas no trecho.
    Devolve (y_corte_em_pontos, quantidade) ou (None, motivo)."""
    pix = renderizar_cinza(doc, trecho, DPI_ANALISE)
    linhas = linhas_de_texto(pix)
    if not linhas:
        return None, "trecho sem conteúdo visível"

    info = []   # (y0, y1, x_do_primeiro_glifo, é_alternativa)
    for y0, y1 in linhas:
        g = primeiros_glifos(pix, y0, y1)
        if g:
            info.append((y0, y1, g[0][0], parece_alternativa(g)))
    # A "margem esquerda" é onde começam as linhas "(A)", "(B)"... (não usamos
    # o ponto mais à esquerda de tudo porque o selo azul fica um pouco mais para fora).
    inicios_alt = [x for _, _, x, alt in info if alt]
    if not inicios_alt:
        return None, "não encontrei as linhas (A), (B)..."
    margem_esq = min(inicios_alt)

    # Andamos de baixo para cima pelas linhas que começam na margem esquerda.
    # Enquanto forem alternativas, fazem parte do grupo final. Linhas recuadas
    # (continuação do texto de uma alternativa) são puladas. Assim, um "(I)"
    # perdido no meio do texto de apoio não é confundido com alternativa.
    grupo = []
    for y0, y1, x, alt in reversed(info):
        if x > margem_esq + 4:
            continue
        if alt:
            grupo.append((y0, y1))
        else:
            break
    if len(grupo) < 2:
        return None, "não encontrei as linhas (A), (B)..."
    if len(grupo) > 5:
        return None, f"encontrei {len(grupo)} linhas parecidas com alternativas (esperado 4 ou 5)"

    primeira = grupo[-1]   # a de cima = (A)
    indice = [l[0] for l in info].index(primeira[0])
    if indice == 0:
        return None, "as alternativas começam no topo do trecho (não sobraria enunciado)"
    fim_anterior = info[indice - 1][1]
    linha_corte = (fim_anterior + primeira[0]) / 2   # no meio do espaço entre as linhas
    zoom = DPI_ANALISE / 72
    return trecho.rect.y0 + linha_corte / zoom, len(grupo)


def tratar_alternativas(doc, q):
    if INCLUIR_ALTERNATIVAS:
        q.alternativas = "incluídas"
        return
    # As alternativas ficam no fim da questão, então olhamos o último trecho.
    ultimo = q.trechos[-1]
    corte, resultado = achar_corte_alternativas(doc, ultimo)
    if corte is None:
        q.alternativas = "MANTIDAS na imagem (não foi possível separar com segurança)"
        q.avisos.append((ultimo.pagina, f"alternativas não removidas: {resultado}"))
        return
    if resultado not in (4, 5):
        q.avisos.append((ultimo.pagina, f"encontrei {resultado} alternativas (o comum é 4 ou 5)"))
    ultimo.rect = pymupdf.Rect(ultimo.rect.x0, ultimo.rect.y0, ultimo.rect.x1, corte)
    q.alternativas = f"removidas ({resultado} detectadas)"


# =====================================================================
# ETAPA 5: gerar as imagens
# =====================================================================

def gerar_imagem(doc, trechos):
    """Empilha os trechos (de cima para baixo) numa página nova e renderiza.
    show_pdf_page copia o conteúdo original do PDF (vetor/imagem), então
    não há perda de qualidade nem distorção. Devolve (bytes_do_jpg, largura, altura)."""
    largura = max(t.rect.width for t in trechos)
    altura = sum(t.rect.height for t in trechos) + ESPACO_ENTRE_TRECHOS * (len(trechos) - 1)
    temp = pymupdf.open()
    pagina = temp.new_page(width=largura, height=altura)
    y = 0
    for t in trechos:
        destino = pymupdf.Rect(0, y, t.rect.width, y + t.rect.height)
        pagina.show_pdf_page(destino, doc, t.pagina, clip=t.rect)
        for r in t.tapar:
            branco = pymupdf.Rect(r.x0 - t.rect.x0, r.y0 - t.rect.y0 + y,
                                  r.x1 - t.rect.x0, r.y1 - t.rect.y0 + y) + (-1, -1, 1, 1)
            pagina.draw_rect(branco, color=None, fill=(1, 1, 1))
        y += t.rect.height + ESPACO_ENTRE_TRECHOS
    pix = pagina.get_pixmap(dpi=DPI, alpha=False)
    jpg = pix.tobytes("jpeg", jpg_quality=QUALIDADE_JPG)
    temp.close()
    return jpg, pix.width, pix.height


def trechos_da_imagem(q):
    """Textos de apoio (na ordem em que aparecem na prova) + a questão."""
    resultado = []
    for a in q.apoios:
        resultado.extend(a.trechos)
    return resultado + q.trechos


# =====================================================================
# ETAPA 6: gabarito e título
# =====================================================================

def procurar_gabarito(doc, total):
    """Procura um gabarito em TEXTO no PDF (ex.: "GABARITO ... 01 - A 02 - C").
    Só aceita se encontrar exatamente uma letra para cada questão.
    Nunca tenta adivinhar respostas."""
    texto = ""
    achou = False
    for pagina in doc:
        t = pagina.get_text()
        if achou or "GABARITO" in t.upper():
            # começa a juntar a partir do título "GABARITO"
            if not achou:
                t = t[t.upper().index("GABARITO"):]
            achou = True
            texto += "\n" + t
    if not achou:
        return None, "a palavra GABARITO não aparece no texto do PDF"
    pares = re.findall(r"\b(\d{1,2})\s*[-–.:)]?\s*\(?([A-E])\)?\b", texto)
    respostas = {}
    for num, letra in pares:
        respostas.setdefault(int(num), letra)
    if all(n in respostas for n in range(1, total + 1)):
        return [respostas[n] for n in range(1, total + 1)], "encontrado no PDF"
    return None, "achei a palavra GABARITO, mas não consegui ler uma letra para cada questão"


def dia_da_prova(doc):
    """Procura "1º dia", "2º DIA", "Dia 2"... na 1ª página (geralmente na linha
    abaixo do título). Devolve "1º dia", "2º dia"... ou "" se não encontrar."""
    texto = doc[0].get_text()
    m = (re.search(r"\b(\d)\s*[ºo°]\s*dia\b", texto, re.IGNORECASE)
         or re.search(r"\bdia\s*(\d)\b", texto, re.IGNORECASE))
    return f"{m.group(1)}º dia" if m else ""


# Títulos genéricos que não identificam a prova (ex.: capa do Vestibulinho).
PADRAO_TITULO_GENERICO = re.compile(r"caderno\s+de\s+quest|folha\s+de\s+resp|instru[çc]|\.indd\b", re.IGNORECASE)


def titulo_da_prova(doc):
    """O texto com a maior fonte da 1ª página (ex.: 'Simulado SARESP 2026 · 6º ano'),
    pulando títulos genéricos como "CADERNO DE QUESTÕES". Se a capa disser o semestre
    ("2º semestre 2026") e o título não, ele é acrescentado."""
    linhas = []
    for bloco in texto_dict(doc[0])["blocks"]:
        for linha in bloco.get("lines", []):
            texto = " ".join("".join(s["text"] for s in linha["spans"]).split())
            tamanho = max((s["size"] for s in linha["spans"] if s["text"].strip()), default=0)
            if texto and not PADRAO_TITULO_GENERICO.search(texto) and re.search(r"[A-Za-zÀ-ÿ]{3}", texto):
                linhas.append((tamanho, texto))
    # Só vale texto curto e maior que o texto comum da página (ex.: Fuvest, capa em imagem:
    # o maior texto é uma linha das instruções; aí fica sem título e o app usa o nome do arquivo).
    tamanhos = [round(t) for t, _ in linhas]
    comum = max(set(tamanhos), key=tamanhos.count) if tamanhos else 0
    linhas = [(t, x) for t, x in linhas if len(x) <= 80 and (round(t) > comum or len(linhas) < 4)]
    if not linhas:
        return ""
    titulo = max(linhas)[1]
    # ENEM: o maior texto da capa é só "2º DIA" (o logotipo é imagem). Usa o ano e o caderno,
    # que dizem qual gabarito vale (cada cor de caderno tem o seu); o app acrescenta o dia.
    texto_capa = doc[0].get_text()
    enem = re.search(r"ENEM\s*(20\d\d)", texto_capa)
    if enem and re.fullmatch(r"\d\s*[ºo°]\s*dia", titulo, re.IGNORECASE):
        caderno = re.search(r"CADERNO\s*(\d+)\s*\|?\s*(AZUL|AMARELO|ROSA|BRANCO|CINZA|VERDE|LARANJA|ROXO)\b",
                            texto_capa, re.IGNORECASE)
        titulo = f"ENEM {enem.group(1)}" + (f" · Caderno {caderno.group(1)} {caderno.group(2).capitalize()}"
                                            if caderno else "")
    # Olimpíadas (OBMEP, ONC...): a capa só diz "Nível 1" ou "Fase 1 - 2025"; o nome da olimpíada
    # está no cabeçalho das folhas (ou no nome do arquivo). Monta "OBMEP 2025 · Nível 1 · 1ª fase".
    texto_doc = " ".join(p.get_text() for p in doc) + " " + (doc.metadata or {}).get("title", "")
    sigla = re.search(r"\b(OBMEP|ONC|OBA|OBF|OBQ|OBB|OBMEP Mirim)(?=\d|\b)", texto_doc)
    if sigla and sigla.group(1).lower() not in titulo.lower() and len(titulo) <= 25:
        ano = re.search(r"\b(20\d\d)\b", titulo + " " + texto_capa) or re.search(r"(20\d\d)", texto_doc)
        nivel = re.search(r"\b(?:n[íi]vel\s*([A-Z0-9]{1,5})|(mirim)\s*(\d))\b", titulo + " " + texto_capa, re.IGNORECASE)
        fase = re.search(r"\bfase\s*(\d)|(\d)\s*[ªa]\s*fase", titulo + " " + texto_capa, re.IGNORECASE)
        partes = [sigla.group(1) + (" " + ano.group(1) if ano else "")]
        if nivel:
            partes.append(f"Nível {nivel.group(1).upper() if nivel.group(1).isalpha() and len(nivel.group(1)) == 1 else nivel.group(1).capitalize()}"
                          if nivel.group(1) else f"Mirim {nivel.group(3)}")
        if fase:
            partes.append(f"{fase.group(1) or fase.group(2)}ª fase")
        titulo = " · ".join(partes)
    # Canguru: "Association Kangourou sans Frontières 2023 – Nível B (Benjamin)" -> "Canguru 2023 · Nível B".
    canguru = re.search(r"Kangourou.*?(20\d\d).*?N[íi]vel\s+([A-Z])\b", titulo)
    if canguru:
        titulo = f"Canguru {canguru.group(1)} · Nível {canguru.group(2)}"
    semestre =re.search(r"\b(\d)\s*[ºo°]?\s*sem(?:estre)?\.?\s*(?:/|de|\|)?\s*(\d{4})?",
                         doc[0].get_text(), re.IGNORECASE)
    if semestre and not re.search(r"sem", titulo, re.IGNORECASE):
        titulo += f" · {semestre.group(1)}º semestre" + (f" {semestre.group(2)}" if semestre.group(2) else "")
    # Versão da prova na capa (ex.: Fuvest, "V1"): ajuda a escolher o gabarito certo.
    versao = re.search(r"^\s*(V\d{1,2}|(?:vers[ãa]o|tipo)\s+\w{1,2})\s*$", doc[0].get_text(), re.IGNORECASE | re.MULTILINE)
    if versao and versao.group(1).lower() not in titulo.lower():
        titulo += f" · {versao.group(1).strip()}"
    return titulo


def texto_answer_key(respostas, total):
    linhas = ["// Gerado automaticamente por extrair_questoes.py"]
    if respostas is None:
        linhas.append(f"// ATENÇÃO: gabarito não encontrado. Preencha as {total} respostas "
                      f"(troque \"\" pela letra).")
        respostas = [""] * total
    linhas.append("const ANSWER_KEY = [")
    for i, r in enumerate(respostas, start=1):
        linhas.append(f'  "{r}", // q{i:02d}')
    linhas.append("];")
    return "\n".join(linhas) + "\n"


# =====================================================================
# ETAPA 7: juntar tudo (usado pelo terminal E pela página web)
# =====================================================================

def extrair(pdf, nome_pdf="prova.pdf", ao_progredir=None):
    """Faz todo o trabalho e devolve um dicionário, sem gravar nada em disco.

    pdf          -> caminho do arquivo OU os bytes do PDF (a página web manda bytes)
    ao_progredir -> função opcional chamada como ao_progredir(feitas, total, mensagem).
    """
    for etapa in extrair_em_etapas(pdf, nome_pdf):
        if etapa[0] == "fim":
            return etapa[1]
        if ao_progredir:
            ao_progredir(*etapa[1:])


def extrair_em_etapas(pdf, nome_pdf="prova.pdf"):
    """Mesma coisa que extrair(), mas como um GERADOR: a cada passo devolve
    ("progresso", feitas, total, mensagem) e, no final, ("fim", resultado).
    A página web usa isso para atualizar a barra de progresso entre um passo
    e outro (sem isso o navegador ficaria "congelado" até o fim)."""
    if isinstance(pdf, (bytes, bytearray, memoryview)):
        doc = pymupdf.open(stream=bytes(pdf), filetype="pdf")
    else:
        doc = pymupdf.open(pdf)

    yield ("progresso", 0, 1, f"Lendo as {len(doc)} páginas da prova")
    # Formatos que o simulado não aceita: avisa o motivo em vez de "não encontrei nenhuma questão".
    linhas_doc = [l.strip() for p in doc for l in p.get_text().splitlines()]
    if sum(len(l) for l in linhas_doc) < 50 * len(doc):
        yield ("fim", {"ok": False, "erro": "Este PDF parece ser escaneado (as páginas são fotos, sem "
                       "texto que dê para ler). Por enquanto só funcionam provas em PDF com texto: "
                       "tente baixar a prova do site oficial.", "avisos_gerais": []})
        return
    # Somatória (UEM, UFSC): alternativas 01, 02, 04, 08, 16 e a resposta é a soma das certas.
    # (numeração comum teria "03)"; a somatória pula do 02 para o 04)
    conta = lambda n: sum(1 for l in linhas_doc if re.match(rf"^{n}\)\s*\S", l))
    if conta("04") >= 5 and conta("08") >= 5 and conta("03") == 0:
        yield ("fim", {"ok": False, "erro": "Esta prova é de SOMATÓRIA (alternativas 01, 02, 04, 08, 16 "
                       "e a resposta é a soma das certas). O simulado só aceita questões com uma "
                       "letra certa (A a E).", "avisos_gerais": []})
        return
    questoes, apoios, avisos_gerais = montar_questoes(doc)
    # Rótulo de questão sem nada embaixo (ex.: um "Questão 14" repetido, escondido na
    # página): não vira questão; só avisa.
    for q in [q for q in questoes if not q.trechos]:
        avisos_gerais.append(f"Página {q.pagina + 1}: \"Questão {q.numero_selo}\" sem conteúdo "
                             f"(ignorada). REVISAR.")
        questoes.remove(q)
    if not questoes:
        yield ("fim", {"ok": False, "erro": "Não encontrei nenhuma questão neste PDF.",
                       "avisos_gerais": avisos_gerais})
        return

    # Conferência da numeração: os selos deveriam ser 1, 2, 3, ... sem pular.
    numeros = [q.numero_selo for q in questoes]
    # Caderno que não começa no 1 (ex.: ENEM 2º dia, questões 91 a 180): vale a numeração da prova.
    primeira = numeros[0] if numeros[0] > 1 and numeros == list(range(numeros[0], numeros[0] + len(questoes))) else 1
    if numeros != list(range(primeira, primeira + len(questoes))):
        avisos_gerais.append(f"A numeração dos selos não é 1..{len(questoes)} em sequência: "
                             f"{numeros}. Os arquivos seguem a ordem do documento. REVISAR.")

    # Quantas alternativas cada questão tem (4 = A-D, 5 = A-E), contando as linhas "(A)", "(B)"...
    # Isso precisa ser feito ANTES de tratar_alternativas, que pode cortar as alternativas da imagem.
    for i, q in enumerate(questoes, start=1):
        yield ("progresso", i - 1, len(questoes), f"Contando as alternativas da questão {i:02d}")
        # Se a prova tem texto de verdade, as linhas "(A)", "(B)"... dizem até qual letra vai.
        letras = {m.group(1) or m.group(2) for t in q.trechos for it in t.itens if it.tipo == "texto"
                  for m in [re.match(r"^\(([A-E])\)|^([A-E])\t", it.texto)] if m}
        letras |= {l for t in q.trechos for it in t.itens if it.tipo == "texto" and re.match(r"^[A-E]\)", it.texto)
                   for l in re.findall(r"(?<![A-Za-z])([A-E])\)", it.texto)}
        # Alternativas em minúscula: "a) 285", "(b) ..." (ex.: Cotuca).
        letras |= {m.group(1).upper() for t in q.trechos for it in t.itens if it.tipo == "texto"
                   for m in [re.match(r"^\(?([a-e])\)(?:\s|$)", it.texto)] if m}
        if {"A", "B", "C", "D"} <= letras:
            q.num_alternativas = 5 if "E" in letras else 4
            continue
        # Senão (questão em imagem), conta as linhas "(A)", "(B)"... pelos pixels.
        corte, quantas = achar_corte_alternativas(doc, q.trechos[-1])
        q.num_alternativas = quantas if corte is not None and quantas in (4, 5) else None
    detectadas = [q.num_alternativas for q in questoes if q.num_alternativas]
    # Questão em que não deu para contar: usa o mais comum na prova (ou 5, se nada foi contado).
    padrao = max(set(detectadas), key=detectadas.count) if detectadas else 5
    for q in questoes:
        if q.num_alternativas is None:
            q.num_alternativas = padrao
            q.avisos.append((q.trechos[-1].pagina, f"não consegui contar as alternativas; "
                                                    f"considerei {padrao} ({'A-D' if padrao == 4 else 'A-E'})"))

    for q in questoes:
        tratar_alternativas(doc, q)

    lista = []
    for i, q in enumerate(questoes, start=1):
        yield ("progresso", i - 1, len(questoes), f"Recortando a questão {i:02d} de {len(questoes)}")
        jpg, w, h = gerar_imagem(doc, trechos_da_imagem(q))
        lista.append({
            "numero": i,
            "selo": q.numero_selo,
            "arquivo": f"q{i:02d}.jpg",
            "assunto": q.assunto,
            "jpg": jpg,
            "largura": w,
            "altura": h,
            "paginas": sorted({t.pagina + 1 for t in trechos_da_imagem(q)}),
            "apoios": [a.texto for a in q.apoios],
            "unida": len(q.trechos) > 1 or any(len(a.trechos) > 1 for a in q.apoios),
            "alternativas": q.alternativas,
            "num_alternativas": q.num_alternativas,   # 4 (A-D) ou 5 (A-E)
            "avisos": [f"pág. {pag + 1}: {msg}" for pag, msg in q.avisos],
            "_q": q,   # objeto interno, usado só pelo relatório
        })
    yield ("progresso", len(questoes), len(questoes), "Pronto")

    respostas, status_gabarito = procurar_gabarito(doc, len(questoes))
    # Caderno com a resposta marcada (ex.: UFPR, "►B) R$ 60,00"): o gabarito vem da marca.
    marcadas = []
    for q in questoes:
        letra = next((m.group(1) for t in q.trechos for it in t.itens if it.tipo == "texto"
                      for m in [re.match(r"^\s*►\s*\(?([A-E])\)", it.texto)] if m), "")
        marcadas.append(letra)
    if respostas is None and sum(1 for l in marcadas if l) >= max(3, 0.6 * len(questoes)):
        respostas = marcadas
        status_gabarito = "respostas marcadas com ► no caderno"
    resultado = {
        "ok": True,
        "titulo": titulo_da_prova(doc),
        "dia": dia_da_prova(doc),
        "paginas": len(doc),
        "questoes": lista,
        "gabarito": respostas,
        "status_gabarito": status_gabarito,
        "avisos_gerais": avisos_gerais,
        "num_apoios": len(apoios),
        "primeira": primeira,   # número da 1ª questão (ENEM 2º dia: 91)
    }
    resultado["relatorio"] = texto_relatorio(nome_pdf, resultado)
    for item in lista:
        del item["_q"]
    _leituras["doc"], _leituras["paginas"] = None, {}   # libera a memória das leituras
    yield ("fim", resultado)


def texto_relatorio(nome_pdf, r):
    questoes = [item["_q"] for item in r["questoes"]]
    arquivos = [(item["arquivo"], item["_q"], item["largura"], item["altura"])
                for item in r["questoes"]]
    respostas, avisos_gerais = r["gabarito"], r["avisos_gerais"]

    L = []
    L.append("RELATÓRIO DE EXTRAÇÃO")
    L.append("=" * 60)
    L.append(f"Arquivo: {nome_pdf}  ({r['paginas']} páginas)")
    if r["titulo"]:
        L.append(f"Prova: {r['titulo']}")
    L.append(f"Total de questões detectadas: {len(questoes)}")
    L.append(f"Total de imagens geradas:     {len(arquivos)}"
             + ("" if len(arquivos) == len(questoes) else "   <-- NÃO BATE! REVISAR"))
    L.append(f"Alternativas na imagem: {'sim' if INCLUIR_ALTERNATIVAS else 'não'}  |  DPI: {DPI}")
    L.append(f"Gabarito: {r['status_gabarito']}"
             + ("" if respostas else f" -> preencher {len(questoes)} respostas"))
    L.append("")

    L.append("ARQUIVOS")
    L.append("-" * 60)
    for nome, q, w, h in arquivos:
        paginas = sorted({t.pagina + 1 for t in trechos_da_imagem(q)})
        L.append(f"{nome}  selo {q.numero_selo:02d}  pág. {','.join(map(str, paginas))}  "
                 f"{w}x{h}px  {q.assunto or '-'}  {'A-D' if q.num_alternativas == 4 else 'A-E'}  alternativas: {q.alternativas}")
    L.append("")

    L.append("TEXTOS DE APOIO INCLUÍDOS")
    L.append("-" * 60)
    com_apoio = [(n, q) for n, q, *_ in arquivos if q.apoios]
    for nome, q in com_apoio:
        partes = "; ".join(
            f"\"{a.texto[:55]}\" (pág. {'+'.join(str(t.pagina + 1) for t in a.trechos)})"
            for a in q.apoios)
        L.append(f"{nome}: {partes}")
    if not com_apoio:
        L.append("(nenhum)")
    compartilhados = {id(a): a for _, q in com_apoio for a in q.apoios if len(a.alvos or []) > 1}
    for a in compartilhados.values():
        L.append(f"  * compartilhado: \"{a.texto[:55]}\" repetido nas questões {a.alvos}")
    L.append("")

    L.append("QUESTÕES UNIDAS (conteúdo continua em outra coluna/página)")
    L.append("-" * 60)
    unidas = 0
    for nome, q, *_ in arquivos:
        grupos = [("apoio", a.trechos) for a in q.apoios] + [("questão", q.trechos)]
        for rotulo, ts in grupos:
            if len(ts) > 1:
                L.append(f"{nome}: {rotulo} em {len(ts)} partes (pág. "
                         f"{' + '.join(str(t.pagina + 1) for t in ts)})")
                unidas += 1
    if not unidas:
        L.append("(nenhuma)")
    L.append("")

    so_imagem = sum(1 for q in questoes
                    if not any(it.tipo == "texto" for t in q.trechos for it in t.itens))
    if so_imagem:
        L.append(f"Obs.: {so_imagem} questões têm o conteúdo só como IMAGEM (sem texto no PDF).")
        L.append("      Dentro dessas imagens o programa não consegue LER frases como")
        L.append("      'questões de 15 a 18'; se houver texto compartilhado assim, confira à mão.")
        L.append("")

    L.append("QUESTÕES COM DIFICULDADE / AVISOS")
    L.append("-" * 60)
    paginas_revisar = set()
    for nome, q, *_ in arquivos:
        for pag, msg in q.avisos:
            L.append(f"{nome} (pág. {pag + 1}): {msg}")
            paginas_revisar.add(pag + 1)
    if not any(q.avisos for q in questoes):
        L.append("(nenhuma)")
    L.append("")

    L.append("AVISOS GERAIS")
    L.append("-" * 60)
    for a in avisos_gerais:
        L.append(a)
        if "REVISAR" in a:
            m = re.search(r"Página (\d+)", a)
            if m:
                paginas_revisar.add(int(m.group(1)))
    if not avisos_gerais:
        L.append("(nenhum)")
    L.append("")

    L.append("PÁGINAS A REVISAR")
    L.append("-" * 60)
    L.append(", ".join(map(str, sorted(paginas_revisar))) if paginas_revisar else "(nenhuma)")
    L.append("")
    L.append("Lembre-se de abrir algumas imagens e conferir visualmente: número certo,")
    L.append("enunciado completo, nada cortado, nada de outra questão junto.")
    return "\n".join(L) + "\n"


# =====================================================================
# PROGRAMA PRINCIPAL (uso pelo terminal)
# =====================================================================

def main():
    arquivo_pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("prova.pdf")
    if not arquivo_pdf.exists():
        print(f"Arquivo não encontrado: {arquivo_pdf}")
        sys.exit(1)

    print(f"PDF: {arquivo_pdf}")
    r = extrair(str(arquivo_pdf), nome_pdf=arquivo_pdf.name)
    if not r["ok"]:
        print(r["erro"], *r["avisos_gerais"], sep="\n  ")
        sys.exit(1)
    print(f"{r['titulo']}{' · ' + r['dia'] if r['dia'] else ''}  ({r['paginas']} páginas)")
    print(f"Questões encontradas: {len(r['questoes'])}  |  textos de apoio: {r['num_apoios']}")

    # Pastas de saída. Apagamos imagens antigas para não sobrar, por exemplo,
    # um q48.jpg de uma prova anterior maior.
    assets = PASTA_SAIDA / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for antigo in assets.glob("q*.jpg"):
        antigo.unlink()

    for item in r["questoes"]:
        (assets / item["arquivo"]).write_bytes(item["jpg"])
        marcas = ("  [+APOIO]" if item["apoios"] else "") + ("  [UNIDA]" if item["unida"] else "") \
            + ("  [AVISO]" if item["avisos"] else "")
        print(f"  {item['arquivo']}  (selo {item['selo']:02d}, pág. {item['paginas'][-1]}, "
              f"{item['largura']}x{item['altura']}px, {item['assunto'] or '-'}){marcas}")

    total = len(r["questoes"])
    (PASTA_SAIDA / "ANSWER_KEY.js").write_text(texto_answer_key(r["gabarito"], total),
                                               encoding="utf-8")
    (PASTA_SAIDA / "relatorio.txt").write_text(r["relatorio"], encoding="utf-8")

    caminho_zip = PASTA_SAIDA / "simulado_extraido.zip"
    with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for item in r["questoes"]:
            z.write(assets / item["arquivo"], f"assets/{item['arquivo']}")
        z.write(PASTA_SAIDA / "ANSWER_KEY.js", "ANSWER_KEY.js")
        z.write(PASTA_SAIDA / "relatorio.txt", "relatorio.txt")

    print(f"\nGabarito: {r['status_gabarito']}")
    if r["gabarito"] is None:
        print(f"  -> {total} respostas precisam ser preenchidas em ANSWER_KEY.js")
    total_avisos = sum(len(i["avisos"]) for i in r["questoes"]) \
        + sum("REVISAR" in a for a in r["avisos_gerais"])
    print(f"Avisos para revisar: {total_avisos}")
    print(f"Pronto! Veja {PASTA_SAIDA / 'relatorio.txt'} e {caminho_zip}")


if __name__ == "__main__":
    main()
