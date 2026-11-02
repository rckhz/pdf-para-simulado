"""
Extrator de questões de prova (PDF) para o simulado online.

Como usar (na pasta do projeto):
    uv run src/escola/extrair_questoes.py              -> usa prova.pdf
    uv run src/escola/extrair_questoes.py outra.pdf    -> usa outro arquivo

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


@dataclass
class Barreira:
    """Algo que marca o início de uma região."""
    tipo: str          # "selo", "instrucao" ou "cabecalho"
    rect: pymupdf.Rect
    numero: int = 0    # número impresso no selo
    texto: str = ""
    alvos: list = None  # instrução: números das questões citadas (None = não diz)


@dataclass
class Trecho:
    """Um retângulo de uma página. Uma questão é formada por 1 ou mais trechos."""
    pagina: int        # índice da página, começando em 0
    rect: pymupdf.Rect
    itens: list = field(default_factory=list)


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


# =====================================================================
# ETAPA 1: ler cada página e separar barreiras, conteúdo e rodapé
# =====================================================================

PADRAO_INICIO_INSTRUCAO = re.compile(
    r"^\s*(leia|observe|analise|considere|examine|utilize|use|com base)\b", re.IGNORECASE)
PADRAO_QUESTOES = re.compile(
    r"\bquest(?:ão|ões|ao|oes)\b\s*((?:de\s+)?\d{1,2}(?:\s*(?:,|e|a|até|ate)\s*\d{1,2})*)?",
    re.IGNORECASE)


def numeros_citados(texto):
    """'questões 05 e 06' -> [5, 6];  'questões de 15 a 18' -> [15, 16, 17, 18];
    'questão.' (sem número) -> None."""
    m = PADRAO_QUESTOES.search(texto)
    if not m or not m.group(1):
        return None
    trecho = re.sub(r"^de\s+", "", m.group(1).strip(), flags=re.IGNORECASE)
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


def e_instrucao(texto, num_linhas):
    """Frase curta do tipo "Leia o texto para responder à questão 05."."""
    return (num_linhas <= 3 and len(texto) < 220
            and PADRAO_INICIO_INSTRUCAO.search(texto) is not None
            and PADRAO_QUESTOES.search(texto) is not None)


def analisar_pagina(pagina):
    """Devolve um dicionário com as barreiras, os itens de conteúdo, as linhas
    divisórias de coluna e o limite inferior útil (topo do rodapé) da página."""
    altura = pagina.rect.height
    barreiras = []
    itens = []
    topo_rodape = altura

    desenhos = [d["rect"] for d in pagina.get_drawings()]
    # A linha fina vertical que separa as colunas não é conteúdo.
    # Uma figura larga pode "cortar" a linha em pedaços curtos; por isso também conta
    # qualquer linha fina vertical bem no meio da página.
    meio = pagina.rect.width / 2
    divisorias = [r for r in desenhos
                  if r.width < 3 and (r.height > 200 or abs((r.x0 + r.x1) / 2 - meio) < 6)]

    # --- Texto ---
    # get_text("dict") devolve blocos -> linhas -> "spans" (trechos com a mesma fonte).
    for bloco in pagina.get_text("dict")["blocks"]:
        if bloco["type"] != 0:   # tipo 0 = texto (imagens tratamos abaixo)
            continue
        linhas_normais = []
        for linha in bloco["lines"]:
            texto_linha = "".join(s["text"] for s in linha["spans"]).strip()
            if not texto_linha:
                continue
            rect_linha = pymupdf.Rect(linha["bbox"])
            primeiro = linha["spans"][0]

            # Rodapé ("Simulado ... 1/11"): não faz parte de questão nenhuma.
            if rect_linha.y0 > altura * FRACAO_RODAPE:
                topo_rodape = min(topo_rodape, rect_linha.y0)
                continue

            fonte_selo = FONTE_SELO in primeiro["font"]
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

            linhas_normais.append((rect_linha, texto_linha))

        if not linhas_normais:
            continue
        # A instrução pode ocupar mais de uma linha, por isso olhamos o bloco todo.
        texto_bloco = " ".join(t for _, t in linhas_normais)
        if e_instrucao(texto_bloco, len(linhas_normais)):
            rect = pymupdf.Rect(linhas_normais[0][0])
            for r, _ in linhas_normais[1:]:
                rect |= r
            barreiras.append(Barreira("instrucao", rect, texto=texto_bloco,
                                      alvos=numeros_citados(texto_bloco)))
        # A instrução também é conteúdo (aparece na imagem).
        for r, t in linhas_normais:
            itens.append(Item(r, "texto", t))

    # --- Imagens ---
    for info in pagina.get_image_info():
        itens.append(Item(pymupdf.Rect(info["bbox"]), "imagem"))

    # --- Desenhos vetoriais (gráficos, tabelas, molduras...) ---
    for r in desenhos:
        if any(r is d for d in divisorias):
            continue
        # Desenhos que pertencem a um selo/cabeçalho já estão na barreira.
        if any(b.tipo != "instrucao" and b.rect.contains(r) for b in barreiras):
            continue
        if r.y0 > topo_rodape:
            continue
        itens.append(Item(pymupdf.Rect(r), "desenho"))

    return {"barreiras": barreiras, "itens": itens, "divisorias": divisorias,
            "topo_rodape": topo_rodape}


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
    paginas = [analisar_pagina(p) for p in doc]
    questoes, apoios, blocos_soltos, avisos_gerais = [], [], [], []

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

    for num_pag, dados in enumerate(paginas):
        if num_pag < pagina_primeira:
            avisos_gerais.append(f"Página {num_pag + 1} ignorada (antes da primeira questão).")
            continue

        largura = doc[num_pag].rect.width
        meio = largura / 2
        fundo = dados["topo_rodape"] - 1

        # Na página da primeira questão, ignoramos tudo acima da primeira
        # barreira (título da prova e cartão-resposta com os números 01..40).
        topo = 0
        if num_pag == pagina_primeira:
            topo = min(b.rect.y0 for b in dados["barreiras"])

        for coluna in (0, 1):
            x0 = 0 if coluna == 0 else meio
            x1 = meio if coluna == 0 else largura

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

                if b.tipo == "selo":
                    dono_atual = Questao(b.numero, num_pag, b.rect, assunto=assunto_atual)
                    questoes.append(dono_atual)
                    for a in apoios:
                        if a.proxima is None:
                            a.proxima = b.numero
                    for a in apoios_sem_alvo:
                        a.alvos = [b.numero]
                    apoios_sem_alvo = []

                elif b.tipo == "instrucao":
                    if instrucao_dentro_da_questao(b, dono_atual, num_pag):
                        continue   # é só uma frase do enunciado; segue com a mesma questão
                    dono_atual = Apoio(num_pag, b.texto, alvos=b.alvos)
                    apoios.append(dono_atual)
                    if b.alvos is None:
                        apoios_sem_alvo.append(dono_atual)

                else:  # cabeçalho de seção: não entra em imagem nenhuma
                    assunto_atual = nome_do_assunto(b.texto)
                    dono_atual = BlocoSolto(num_pag)
                    blocos_soltos.append(dono_atual)
                    cursor = b.rect.y1

            # Da última barreira até o fim da coluna.
            adicionar_regiao(num_pag, dados, coluna, meio, x0, x1, cursor, fundo,
                             dono_atual, avisos_gerais)

    # Recorta cada trecho justo ao redor do conteúdo.
    for q in questoes:
        q.trechos = finalizar_trechos(doc, paginas, q.trechos, q.avisos)
    for a in apoios:
        avisos_apoio = []
        a.trechos = finalizar_trechos(doc, paginas, a.trechos, avisos_apoio)
        for pag, msg in avisos_apoio:
            avisos_gerais.append(f"Página {pag + 1}: texto de apoio ({a.texto[:50]}...): {msg}. REVISAR.")

    ligar_apoios(questoes, apoios, avisos_gerais)

    for bloco in blocos_soltos:
        if bloco.trechos:
            avisos_gerais.append(
                f"Página {bloco.pagina + 1}: há conteúdo logo após um cabeçalho de seção que não "
                f"pertence a nenhuma questão (não foi usado). REVISAR."
            )
    return questoes, apoios, avisos_gerais


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
        if coluna_de(it.rect, meio) == coluna and y0 <= (it.rect.y0 + it.rect.y1) / 2 < y1
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
        largura = doc[t.pagina].rect.width
        meio = largura / 2
        conteudo = pymupdf.Rect(t.itens[0].rect)
        for it in t.itens[1:]:
            conteudo |= it.rect

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
        for e in estranhos:
            inter = e & recorte
            if inter.is_empty or inter.width < 0.5 or inter.height < 0.5:
                continue
            if e.y1 <= conteudo.y0 + 0.5:
                recorte.y0 = max(recorte.y0, e.y1)
            elif e.y0 >= conteudo.y1 - 0.5:
                recorte.y1 = min(recorte.y1, e.y0)
            elif e.x1 <= conteudo.x0 + 0.5:
                recorte.x0 = max(recorte.x0, e.x1)
            elif e.x0 >= conteudo.x1 - 0.5:
                recorte.x1 = min(recorte.x1, e.x0)
            else:
                avisos.append((t.pagina, f"conteúdo de fora se sobrepõe ao recorte "
                                         f"({_fmt(e)}); possível mistura de questões"))
        finais.append(Trecho(t.pagina, recorte, t.itens))
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


def titulo_da_prova(doc):
    """O texto com a maior fonte da 1ª página (ex.: 'Simulado SARESP 2026 · 6º ano')."""
    maior, titulo = 0, ""
    for bloco in doc[0].get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for span in linha["spans"]:
                if span["text"].strip() and span["size"] > maior:
                    maior = span["size"]
                    titulo = "".join(s["text"] for s in linha["spans"]).strip()
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
    questoes, apoios, avisos_gerais = montar_questoes(doc)
    if not questoes:
        yield ("fim", {"ok": False, "erro": "Não encontrei nenhuma questão neste PDF.",
                       "avisos_gerais": avisos_gerais})
        return

    # Conferência da numeração: os selos deveriam ser 1, 2, 3, ... sem pular.
    numeros = [q.numero_selo for q in questoes]
    if numeros != list(range(1, len(questoes) + 1)):
        avisos_gerais.append(f"A numeração dos selos não é 1..{len(questoes)} em sequência: "
                             f"{numeros}. Os arquivos seguem a ordem do documento. REVISAR.")

    # Quantas alternativas cada questão tem (4 = A-D, 5 = A-E), contando as linhas "(A)", "(B)"...
    # Isso precisa ser feito ANTES de tratar_alternativas, que pode cortar as alternativas da imagem.
    for i, q in enumerate(questoes, start=1):
        yield ("progresso", i - 1, len(questoes), f"Contando as alternativas da questão {i:02d}")
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
    }
    resultado["relatorio"] = texto_relatorio(nome_pdf, resultado)
    for item in lista:
        del item["_q"]
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
