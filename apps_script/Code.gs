/**
 * Simulado Online — backend (Google Apps Script)
 *
 * DOIS LINKS, UM SÓ APP:
 *   .../exec?professor  -> Área do Professor (envia o PDF, marca o gabarito, publica, vê notas)
 *   .../exec?prova=ID   -> página do aluno para um simulado específico
 *   .../exec            -> página do aluno (mostra os simulados abertos)
 *
 * Tudo é criado automaticamente na primeira vez:
 *   - planilha "Simulados - Resultados" (aba "Provas" + uma aba de respostas por simulado)
 *   - pasta "Simulados - Imagens das Questões" (uma subpasta por simulado)
 *
 * O professor nunca precisa abrir este código. O recorte das questões é feito
 * no navegador do professor (Professor.html + Extrator.html), e as imagens
 * chegam aqui já prontas.
 *
 * Segurança: o gabarito não vai para a página do aluno antes do envio. Depois que ele envia,
 * a correção mostra a letra certa de cada questão (padrão; o professor pode desligar em
 * Configurações). As funções da Área do Professor exigem a senha (token de sessão).
 */

const NOME_PLANILHA = 'Simulados - Resultados';
const NOME_PASTA = 'Simulados - Imagens das Questões';
const ABA_PROVAS = 'Provas';
const COLUNAS_PROVAS = ['ID', 'Nome', 'Questões', 'Situação', 'Criado em', 'Pasta', 'Aba de respostas',
                        'Gabarito', 'Matérias', 'Imagens', 'Avisos', 'Alternativas', 'Primeira'];
const LETRAS = ['A', 'B', 'C', 'D', 'E'];
const ANULADA = 'X';   // no gabarito: questão anulada (não conta na nota; a prova vale menos)
const valeNoGabarito_ = l => LETRAS.indexOf(l) !== -1 || l === ANULADA;
const COLUNAS_FIXAS_RESPOSTAS = ['Data/hora', 'Nome completo', 'Turma', 'Acertos', 'Nota (0-10)', 'Situação'];
const SITUACAO = { RASCUNHO: 'Rascunho', PUBLICADO: 'Publicado', ENCERRADO: 'Encerrado' };
const DURACAO_SESSAO_SEG = 6 * 60 * 60;   // o professor fica logado por 6 horas
const VERDE = '#d9f2d9', VERMELHO = '#f8d7d7', CINZA = '#eeeeee';


// =====================================================================
// PÁGINAS
// =====================================================================

function doGet(e) {
  const p = (e && e.parameter) || {};
  let pagina;
  if ('professor' in p) {
    pagina = HtmlService.createTemplateFromFile('Professor').evaluate().setTitle('Área do Professor · Simulados');
  } else {
    const t = HtmlService.createTemplateFromFile('Aluno');
    t.provaId = String(p.prova || '');
    pagina = t.evaluate().setTitle('Simulado');
  }
  return pagina
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** Usado nos arquivos .html para incluir outro arquivo (ex.: o Extrator). */
function include(nome) {
  return HtmlService.createHtmlOutputFromFile(nome).getContent();
}


// =====================================================================
// ARMAZENAMENTO (planilha + pasta, criadas sozinhas)
// =====================================================================

function props_() { return PropertiesService.getScriptProperties(); }

function planilha_() {
  const id = props_().getProperty('PLANILHA_ID');
  if (id) {
    try { return SpreadsheetApp.openById(id); } catch (e) { /* apagada: cria outra */ }
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const id2 = props_().getProperty('PLANILHA_ID');
    if (id2 && id2 !== id) return SpreadsheetApp.openById(id2);
    const ss = SpreadsheetApp.create(NOME_PLANILHA);
    const aba = ss.getSheets()[0];
    aba.setName(ABA_PROVAS);
    aba.getRange(1, 1, 1, COLUNAS_PROVAS.length).setValues([COLUNAS_PROVAS]).setFontWeight('bold');
    aba.setFrozenRows(1);
    props_().setProperty('PLANILHA_ID', ss.getId());
    return ss;
  } finally {
    lock.releaseLock();
  }
}

function pastaRaiz_() {
  const id = props_().getProperty('PASTA_ID');
  if (id) {
    try {
      const pasta = DriveApp.getFolderById(id);
      if (!pasta.isTrashed()) return pasta;
    } catch (e) { /* apagada: cria outra */ }
  }
  const pasta = DriveApp.createFolder(NOME_PASTA);
  props_().setProperty('PASTA_ID', pasta.getId());
  return pasta;
}

function abaProvas_() { return planilha_().getSheetByName(ABA_PROVAS); }

function lerJson_(texto, padrao) {
  try { return texto ? JSON.parse(texto) : padrao; } catch (e) { return padrao; }
}

/** Lê todas as provas da aba "Provas". */
function lerProvas_() {
  const aba = abaProvas_();
  const n = aba.getLastRow() - 1;
  if (n < 1) return [];
  return aba.getRange(2, 1, n, COLUNAS_PROVAS.length).getValues().map((v, i) => ({
    linha: i + 2,
    id: String(v[0]),
    nome: String(v[1]),
    total: Number(v[2]),
    situacao: String(v[3]),
    criadoEm: v[4] instanceof Date ? v[4].toISOString() : String(v[4]),
    pastaId: String(v[5]),
    abaRespostas: String(v[6]),
    gabarito: lerJson_(v[7], []),
    materias: lerJson_(v[8], []),
    imagens: lerJson_(v[9], []),
    avisos: lerJson_(v[10], {}),
    // quantas alternativas cada questão tem (4 = A-D, 5 = A-E); simulados antigos: todas 5
    alternativas: lerJson_(v[11], []),
    // número da 1ª questão (ex.: ENEM 2º dia começa na 91); simulados antigos: 1
    primeira: Number(v[12]) > 1 ? Number(v[12]) : 1
  })).filter(p => p.id);
}

/** Busca uma prova pelo ID. Usa um cache curto para a página do aluno ficar rápida. */
function acharProva_(id, semCache) {
  const cache = CacheService.getScriptCache();
  if (!semCache) {
    const guardada = cache.get('prova_' + id);
    if (guardada) return JSON.parse(guardada);
  }
  const prova = lerProvas_().find(p => p.id === String(id));
  if (!prova) throw new Error('Simulado não encontrado.');
  try { cache.put('prova_' + id, JSON.stringify(prova), 300); } catch (e) { /* grande demais: sem cache */ }
  return prova;
}

function salvarCampos_(prova, campos) {
  const aba = abaProvas_();
  Object.keys(campos).forEach(coluna => {
    let valor = campos[coluna];
    if (typeof valor === 'object') valor = JSON.stringify(valor);
    aba.getRange(prova.linha, COLUNAS_PROVAS.indexOf(coluna) + 1).setValue(valor);
  });
  CacheService.getScriptCache().remove('prova_' + prova.id);
}

function gabaritoCompleto_(prova) {
  return prova.gabarito.length === prova.total && prova.gabarito.every(valeNoGabarito_);
}

function linkAluno_(id) {
  return ScriptApp.getService().getUrl() + '?prova=' + encodeURIComponent(id);
}


// =====================================================================
// SENHA DO PROFESSOR
// =====================================================================

function hash_(senha) {
  let sal = props_().getProperty('SAL');
  if (!sal) { sal = Utilities.getUuid(); props_().setProperty('SAL', sal); }
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, sal + senha, Utilities.Charset.UTF_8);
  return Utilities.base64Encode(bytes);
}

function novaSessao_() {
  const token = Utilities.getUuid();
  CacheService.getScriptCache().put('sessao_' + token, '1', DURACAO_SESSAO_SEG);
  return token;
}

function exigirProfessor_(token) {
  if (!token || !CacheService.getScriptCache().get('sessao_' + token)) {
    throw new Error('SESSAO_EXPIRADA');
  }
}

/** A página pergunta se já existe senha (para mostrar "Criar senha" ou "Entrar"). */
function professorTemSenha() {
  return !!props_().getProperty('SENHA_HASH');
}

function criarSenhaProfessor(senha) {
  senha = String(senha || '');
  if (senha.length < 4) throw new Error('A senha precisa ter pelo menos 4 caracteres.');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    if (professorTemSenha()) throw new Error('A senha já foi criada. Use "Entrar".');
    props_().setProperty('SENHA_HASH', hash_(senha));
  } finally {
    lock.releaseLock();
  }
  return novaSessao_();
}

function entrarProfessor(senha) {
  Utilities.sleep(400);   // atrasa um pouco tentativas de adivinhar a senha
  if (hash_(String(senha || '')) !== props_().getProperty('SENHA_HASH')) {
    throw new Error('Senha incorreta.');
  }
  return novaSessao_();
}

/**
 * SEGURANÇA: toda função deste arquivo cujo nome NÃO termina com "_" pode ser chamada por
 * qualquer pessoa que abrir o link (pelo console do navegador, com google.script.run).
 * As funções feitas para o dono rodar no editor (redefinir senha, autorizar) chamam esta
 * proteção: quem abre o link sem ser o dono (aluno, visitante) recebe um erro.
 * (Para um visitante, o Google não informa o e-mail: getActiveUser() vem vazio.)
 * O dono é o dono do arquivo do projeto no Drive (Session.getEffectiveUser() pediria uma
 * permissão a mais, que o app não tem, e daria erro até para o dono).
 */
function soNoEditor_() {
  const quem = Session.getActiveUser().getEmail();
  let dono = '';
  try { dono = DriveApp.getFileById(ScriptApp.getScriptId()).getOwner().getEmail(); } catch (e) {}
  if (!quem || !dono || quem !== dono) {
    throw new Error('Esta função só pode ser executada pelo dono do projeto, no editor do Apps Script.');
  }
}

/**
 * Esqueceu a senha? Abra o editor do Apps Script, escolha esta função no menu
 * e clique em "Executar". Na próxima visita à Área do Professor, ela pedirá
 * uma senha nova. Nenhum simulado ou resultado é apagado.
 */
function redefinirSenhaProfessor() {
  soNoEditor_();
  props_().deleteProperty('SENHA_HASH');
  Logger.log('Senha apagada. Abra a Área do Professor para criar uma nova.');
}

/**
 * Execute UMA vez no editor depois de atualizar o projeto: faz o Google pedir
 * as permissões novas (ler gabarito de foto/PDF usa o Google Docs e a Drive API).
 */
function autorizarPermissoes() {
  soNoEditor_();
  DriveApp.getRootFolder();
  DocumentApp.getActiveDocument();
  Drive.Files.list({ pageSize: 1 });
  Logger.log('Tudo autorizado. Agora publique uma nova versão (Implantar > Gerenciar implantações).');
}


// =====================================================================
// ÁREA DO PROFESSOR
// =====================================================================

/** Lista de simulados para a tela inicial do professor. */
function painelProfessor(token) {
  exigirProfessor_(token);
  const ss = planilha_();
  const provas = lerProvas_().reverse().map(p => {
    const aba = ss.getSheetByName(p.abaRespostas);
    return {
      id: p.id, nome: p.nome, total: p.total, situacao: p.situacao, criadoEm: p.criadoEm,
      envios: aba ? Math.max(0, aba.getLastRow() - 1) : 0,
      faltamGabarito: p.total - p.gabarito.filter(valeNoGabarito_).length
    };
  });
  return {
    provas: provas,
    planilhaUrl: ss.getUrl(),
    email: props_().getProperty('EMAIL_NOTIFICACAO') || '',
    mostrarCerta: mostrarCertaAoAluno_()
  };
}

/**
 * Passo 1 do envio de um PDF: cria a subpasta, a aba de respostas e a linha
 * na aba "Provas" (como Rascunho). As imagens vêm depois, uma por vez.
 */
function criarProva(token, dados) {
  exigirProfessor_(token);
  const total = Number(dados.total);
  if (!(total > 0 && total <= 200)) throw new Error('Quantidade de questões inválida.');
  const nome = String(dados.nome || 'Simulado').trim().slice(0, 120) || 'Simulado';
  const id = Utilities.getUuid().slice(0, 8);
  const criadoEm = new Date();

  const pasta = pastaRaiz_().createFolder(nome + ' (' + Utilities.formatDate(criadoEm,
    Session.getScriptTimeZone(), 'dd-MM-yyyy HH.mm') + ')');
  if (dados.relatorio) pasta.createFile('relatorio.txt', String(dados.relatorio), MimeType.PLAIN_TEXT);

  // Aba de respostas deste simulado. Nomes de aba não aceitam alguns símbolos.
  const ss = planilha_();
  const base = ('Respostas · ' + nome).replace(/[\[\]\*\?\/\\:]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 90);
  let nomeAba = base, k = 2;
  while (ss.getSheetByName(nomeAba)) nomeAba = base + ' (' + (k++) + ')';
  const aba = ss.insertSheet(nomeAba);
  const primeira = Math.max(1, Math.min(200, Math.floor(Number(dados.primeira) || 1)));
  const cab = COLUNAS_FIXAS_RESPOSTAS.slice();
  for (let i = 1; i <= total; i++) cab.push('Q' + String(i + primeira - 1).padStart(2, '0'));
  aba.getRange(1, 1, 1, cab.length).setValues([cab]).setFontWeight('bold');
  aba.setFrozenRows(1);
  aba.setFrozenColumns(2);

  const gabarito = [];
  for (let i = 0; i < total; i++) {
    const l = dados.gabarito && dados.gabarito[i];
    gabarito.push(valeNoGabarito_(l) ? l : '');
  }

  const alternativas = [];
  for (let i = 0; i < total; i++) alternativas.push((dados.alternativas || [])[i] === 4 ? 4 : 5);

  const abaP = abaProvas_();
  // planilhas criadas antes desta versão não têm o título das colunas mais novas
  ['Alternativas', 'Primeira'].forEach(titulo => {
    const col = COLUNAS_PROVAS.indexOf(titulo) + 1;
    if (abaP.getRange(1, col).getValue() !== titulo) abaP.getRange(1, col).setValue(titulo).setFontWeight('bold');
  });
  abaP.appendRow([id, nome, total, SITUACAO.RASCUNHO, criadoEm, pasta.getId(), nomeAba,
    JSON.stringify(gabarito), JSON.stringify(dados.materias || []), '[]', JSON.stringify(dados.avisos || {}),
    JSON.stringify(alternativas), primeira]);
  return id;
}

/** Passo 2: recebe UMA imagem (em base64) e salva na pasta do simulado. */
function salvarImagem(token, provaId, numero, base64) {
  exigirProfessor_(token);
  const prova = acharProva_(provaId, true);
  const nome = 'q' + String(numero).padStart(2, '0') + '.jpg';
  const pasta = DriveApp.getFolderById(prova.pastaId);
  const antigos = pasta.getFilesByName(nome);
  while (antigos.hasNext()) antigos.next().setTrashed(true);
  const blob = Utilities.newBlob(Utilities.base64Decode(base64), 'image/jpeg', nome);
  return pasta.createFile(blob).getId();
}

/** Passo 3: guarda a lista com os IDs das imagens (na ordem das questões). */
function concluirImagens(token, provaId, ids) {
  exigirProfessor_(token);
  const prova = acharProva_(provaId, true);
  if (!Array.isArray(ids) || ids.length !== prova.total || ids.some(x => !x)) {
    throw new Error('Nem todas as imagens foram salvas. Tente enviar o PDF de novo.');
  }
  salvarCampos_(prova, { 'Imagens': ids });
  return true;
}

/** Tudo o que a tela de um simulado precisa (inclui o gabarito: só o professor vê). */
function abrirProva(token, provaId) {
  exigirProfessor_(token);
  const p = acharProva_(provaId, true);
  const aba = planilha_().getSheetByName(p.abaRespostas);
  return {
    id: p.id, nome: p.nome, total: p.total, situacao: p.situacao, criadoEm: p.criadoEm,
    gabarito: p.gabarito, materias: p.materias, avisos: p.avisos, alternativas: p.alternativas,
    primeira: p.primeira, imagensOk: p.imagens.length === p.total,
    envios: aba ? Math.max(0, aba.getLastRow() - 1) : 0,
    link: linkAluno_(p.id),
    pastaUrl: 'https://drive.google.com/drive/folders/' + p.pastaId
  };
}

function imagemProfessor(token, provaId, numero) {
  exigirProfessor_(token);
  return imagemBase64_(acharProva_(provaId), numero);
}

/** Salva o gabarito e recalcula as notas de quem já enviou. */
function salvarGabarito(token, provaId, gabarito) {
  exigirProfessor_(token);
  const prova = acharProva_(provaId, true);
  const limpo = [];
  for (let i = 0; i < prova.total; i++) {
    const l = String((gabarito && gabarito[i]) || '').toUpperCase();
    limpo.push(valeNoGabarito_(l) ? l : '');
  }
  salvarCampos_(prova, { 'Gabarito': limpo });
  prova.gabarito = limpo;
  const corrigidos = recorrigir_(prova);
  return { faltam: limpo.filter(l => !l).length, corrigidos: corrigidos };
}

/**
 * Lê o texto de uma FOTO ou PDF de gabarito com o reconhecimento de texto (OCR)
 * do Google Drive: o arquivo vira um Google Docs temporário, lemos o texto e
 * jogamos o documento na lixeira. Quem transforma o texto em respostas é a
 * página do professor (interpretarGabarito), que mostra tudo para conferência.
 * Precisa do serviço avançado "Drive API" (já declarado no appsscript.json).
 */
function lerTextoDeArquivo(token, nome, tipo, base64) {
  exigirProfessor_(token);
  if (typeof Drive === 'undefined') {
    throw new Error('O serviço "Drive API" não está ativado no projeto do Apps Script (veja o LEIA-ME).');
  }
  const blob = Utilities.newBlob(Utilities.base64Decode(base64), tipo || 'application/octet-stream', nome);
  if (ePlanilha_(nome, tipo)) return textoDePlanilha_(blob);
  const doc = Drive.Files.create({ name: 'gabarito-temporario (pode apagar)', mimeType: MimeType.GOOGLE_DOCS },
                                 blob, { ocrLanguage: 'pt' });
  try {
    return DocumentApp.openById(doc.id).getBody().getText();
  } finally {
    try { DriveApp.getFileById(doc.id).setTrashed(true); } catch (e) { /* já foi apagado */ }
  }
}

function ePlanilha_(nome, tipo) {
  return /\.(xlsx|xlsm|xls|ods)$/i.test(String(nome)) ||
    /spreadsheet|ms-excel|opendocument\.spreadsheet/i.test(String(tipo));
}

/**
 * Planilha (Excel .xlsx/.xls ou .ods): o Drive converte para Planilha Google e lemos
 * todas as abas como texto, linha por linha ("01 A", ou uma tabela Questão | Resposta).
 * O nome de cada aba vem antes (ex.: "6º ano"), para ajudar a dar nome aos gabaritos.
 */
function textoDePlanilha_(blob) {
  const arq = Drive.Files.create({ name: 'gabarito-temporario (pode apagar)', mimeType: MimeType.GOOGLE_SHEETS }, blob);
  try {
    const linhas = [];
    SpreadsheetApp.openById(arq.id).getSheets().forEach(aba => {
      if (aba.getLastRow() < 1 || aba.getLastColumn() < 1) return;
      linhas.push(aba.getName());
      aba.getRange(1, 1, aba.getLastRow(), aba.getLastColumn()).getDisplayValues().forEach(l => {
        // célula só com "a".."e" vira maiúscula (o intérprete procura letras A-E)
        const celulas = l.map(v => String(v).trim()).map(v => /^\(?[a-e]\)?$/.test(v) ? v.toUpperCase() : v);
        if (celulas.some(Boolean)) linhas.push(celulas.join('   '));
      });
    });
    return linhas.join('\n');
  } finally {
    try { DriveApp.getFileById(arq.id).setTrashed(true); } catch (e) { /* já foi apagado */ }
  }
}

function mudarSituacao(token, provaId, situacao) {
  exigirProfessor_(token);
  if ([SITUACAO.PUBLICADO, SITUACAO.ENCERRADO].indexOf(situacao) === -1) throw new Error('Situação inválida.');
  const prova = acharProva_(provaId, true);
  if (prova.imagens.length !== prova.total) throw new Error('As imagens deste simulado não foram salvas por completo.');
  salvarCampos_(prova, { 'Situação': situacao });
  return situacao;
}

function renomearProva(token, provaId, nome) {
  exigirProfessor_(token);
  nome = String(nome || '').trim().slice(0, 120);
  if (!nome) throw new Error('Digite um nome.');
  salvarCampos_(acharProva_(provaId, true), { 'Nome': nome });
  return nome;
}

/** Notas dos alunos + quais questões a turma mais errou. */
function resultados(token, provaId) {
  exigirProfessor_(token);
  const prova = acharProva_(provaId, true);
  const ss = planilha_();
  const aba = ss.getSheetByName(prova.abaRespostas);
  const n = aba ? aba.getLastRow() - 1 : 0;
  const saida = { alunos: [], questoes: [], planilhaUrl: ss.getUrl() + '#gid=' + (aba ? aba.getSheetId() : 0),
                  gabaritoCompleto: gabaritoCompleto_(prova) };
  if (n < 1) return saida;

  const fixas = COLUNAS_FIXAS_RESPOSTAS.length;
  const linhas = aba.getRange(2, 1, n, fixas + prova.total).getValues();
  const contagem = [];
  for (let i = 0; i < prova.total; i++) contagem.push({ numero: i + 1, acertos: 0, marcadas: {} });

  linhas.forEach(v => {
    saida.alunos.push({
      data: v[0] instanceof Date ? v[0].toISOString() : String(v[0]),
      nome: String(v[1]), turma: String(v[2]), acertos: v[3], nota: v[4], situacao: String(v[5])
    });
    for (let i = 0; i < prova.total; i++) {
      const r = String(v[fixas + i] || '');
      const c = contagem[i];
      c.marcadas[r || '—'] = (c.marcadas[r || '—'] || 0) + 1;
      if (r && r === prova.gabarito[i]) c.acertos++;
    }
  });
  saida.questoes = contagem.map(c => ({
    numero: c.numero,
    correta: prova.gabarito[c.numero - 1] || '',
    percentual: Math.round(c.acertos / n * 100),
    marcadas: c.marcadas
  }));
  return saida;
}

/** Apaga um simulado: a pasta vai para a lixeira do Drive e a aba é removida. */
function excluirProva(token, provaId) {
  exigirProfessor_(token);
  const prova = acharProva_(provaId, true);
  try { DriveApp.getFolderById(prova.pastaId).setTrashed(true); } catch (e) { /* já não existe */ }
  const ss = planilha_();
  const aba = ss.getSheetByName(prova.abaRespostas);
  if (aba && ss.getSheets().length > 1) ss.deleteSheet(aba);
  abaProvas_().deleteRow(prova.linha);
  CacheService.getScriptCache().remove('prova_' + prova.id);
  return true;
}

/** O aluno vê a letra certa na correção? (padrão: sim; o professor pode desligar) */
function mostrarCertaAoAluno_() {
  return props_().getProperty('MOSTRAR_CERTA') !== 'nao';
}

function configurarMostrarCerta(token, mostrar) {
  exigirProfessor_(token);
  props_().setProperty('MOSTRAR_CERTA', mostrar ? 'sim' : 'nao');
  return !!mostrar;
}

function configurarEmail(token, email) {
  exigirProfessor_(token);
  email = String(email || '').trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('Digite um e-mail válido.');
  if (email) props_().setProperty('EMAIL_NOTIFICACAO', email);
  else props_().deleteProperty('EMAIL_NOTIFICACAO');
  return email;
}


// =====================================================================
// CORREÇÃO
// =====================================================================

/** Calcula acertos/nota de uma lista de respostas. Devolve null se o gabarito estiver incompleto. */
function corrigir_(prova, respostas) {
  if (!gabaritoCompleto_(prova)) return null;
  // Questão anulada não conta: nem como acerto, nem como erro (90 questões com 2 anuladas valem 88).
  const validas = prova.gabarito.slice(0, prova.total).filter(l => l !== ANULADA).length;
  const acertos = respostas.reduce((t, r, i) => t + (r && r === prova.gabarito[i] ? 1 : 0), 0);
  return { acertos: acertos, validas: validas, nota: validas ? Math.round(acertos / validas * 100) / 10 : 0 };
}

function cores_(prova, respostas, corrigido) {
  return respostas.map((r, i) => !corrigido || prova.gabarito[i] === ANULADA ? null
    : (r === prova.gabarito[i] ? VERDE : (r ? VERMELHO : CINZA)));
}

/** Recalcula todas as notas da aba (chamado sempre que o gabarito muda). */
function recorrigir_(prova) {
  const aba = planilha_().getSheetByName(prova.abaRespostas);
  if (!aba || aba.getLastRow() < 2) return 0;
  const n = aba.getLastRow() - 1;
  const fixas = COLUNAS_FIXAS_RESPOSTAS.length;
  const respostas = aba.getRange(2, fixas + 1, n, prova.total).getValues()
    .map(l => l.map(v => String(v || '').trim().toUpperCase()));
  const notas = [], cores = [];
  respostas.forEach(r => {
    const c = corrigir_(prova, r);
    notas.push(c ? [c.acertos, c.nota, 'Corrigido'] : ['', '', 'Aguardando gabarito']);
    cores.push(cores_(prova, r, !!c));
  });
  aba.getRange(2, 4, n, 3).setValues(notas);
  aba.getRange(2, fixas + 1, n, prova.total).setBackgrounds(cores);
  return n;
}


// =====================================================================
// PÁGINA DO ALUNO
// =====================================================================

/** Simulados abertos (para quando o aluno usa o link sem ?prova=). */
function simuladosAbertos() {
  return lerProvas_()
    .filter(p => p.situacao === SITUACAO.PUBLICADO)
    .reverse()
    .map(p => ({ id: p.id, nome: p.nome, total: p.total }));
}

/** Dados públicos de um simulado. NÃO inclui o gabarito. */
function dadosSimulado(provaId) {
  const p = acharProva_(provaId);
  if (p.situacao !== SITUACAO.PUBLICADO) {
    throw new Error(p.situacao === SITUACAO.ENCERRADO
      ? 'Este simulado já foi encerrado pelo professor.'
      : 'Este simulado ainda não foi liberado pelo professor.');
  }
  return { id: p.id, nome: p.nome, total: p.total, materias: p.materias, alternativas: p.alternativas,
           primeira: p.primeira };
}

function imagemQuestao(provaId, numero) {
  const p = acharProva_(provaId);
  if (p.situacao !== SITUACAO.PUBLICADO) throw new Error('Este simulado não está aberto.');
  return imagemBase64_(p, numero);
}

function imagemBase64_(prova, numero) {
  const id = prova.imagens[Number(numero) - 1];
  if (!id) throw new Error('Imagem da questão ' + numero + ' não encontrada.');
  const blob = DriveApp.getFileById(id).getBlob();
  return 'data:' + blob.getContentType() + ';base64,' + Utilities.base64Encode(blob.getBytes());
}

function enviarRespostas(payload) {
  if (!payload || !payload.provaId) throw new Error('Simulado não informado.');
  const nome = String(payload.nome || '').trim();
  const turma = String(payload.turma || '').trim();
  if (!nome || !turma) throw new Error('Preencha nome e turma.');

  const prova = acharProva_(payload.provaId, true);
  if (prova.situacao !== SITUACAO.PUBLICADO) throw new Error('Este simulado não está mais aberto.');

  const respostas = [];
  for (let i = 1; i <= prova.total; i++) {
    const r = String((payload.respostas || {})[String(i)] || '').toUpperCase();
    const limite = prova.alternativas[i - 1] === 4 ? 4 : 5;   // questão A-D não aceita "E"
    if (r && (LETRAS.indexOf(r) === -1 || LETRAS.indexOf(r) >= limite)) {
      throw new Error('Alternativa inválida na questão ' + i + '.');
    }
    respostas.push(r);   // em branco = conta como errada
  }

  const c = corrigir_(prova, respostas);
  const quando = new Date();
  const aba = planilha_().getSheetByName(prova.abaRespostas);
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    aba.appendRow([quando, comoTexto_(nome), comoTexto_(turma), c ? c.acertos : '', c ? c.nota : '',
                   c ? 'Corrigido' : 'Aguardando gabarito'].concat(respostas));
    const linha = aba.getLastRow();
    if (c) aba.getRange(linha, COLUNAS_FIXAS_RESPOSTAS.length + 1, 1, prova.total)
      .setBackgrounds([cores_(prova, respostas, true)]);
  } finally {
    lock.releaseLock();
  }

  avisarPorEmail_(prova, nome, turma, quando, c);
  return {
    corrigido: !!c,
    acertos: c ? c.acertos : '',
    erros: c ? c.validas - c.acertos : '',
    total: c ? c.validas : prova.total,   // sem as anuladas
    anuladas: c ? prova.total - c.validas : 0,
    nota: c ? c.nota : '',
    // Para a tela de correção do aluno: se acertou/errou/deixou em branco cada questão.
    correcao: c ? respostas.map((r, i) => prova.gabarito[i] === ANULADA ? 'anulada'
      : (!r ? 'branco' : (r === prova.gabarito[i] ? 'certa' : 'errada'))) : [],
    // Letra certa de cada questão: vai por padrão (os simulados são treino); o professor pode
    // desligar em ⚙ Configurações da Área do Professor.
    certas: c && mostrarCertaAoAluno_() ? prova.gabarito.slice(0, prova.total) : []
  };
}

/**
 * O Google Planilhas trata como FÓRMULA o texto que começa com = + - ou @
 * ("-Ana" vira #ERROR!, "=..." executaria uma fórmula). O apóstrofo na frente
 * faz a planilha guardar como texto puro (ele não aparece na célula).
 */
function comoTexto_(valor) {
  const s = String(valor);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function avisarPorEmail_(prova, nome, turma, quando, c) {
  const para = props_().getProperty('EMAIL_NOTIFICACAO');
  if (!para) return;
  try {
    MailApp.sendEmail({
      to: para,
      subject: 'Novo envio - ' + prova.nome,
      body: 'Um aluno enviou as respostas.\n\nSimulado: ' + prova.nome + '\nNome: ' + nome +
            '\nTurma: ' + turma + '\nData: ' + quando.toLocaleString('pt-BR') +
            '\nResultado: ' + (c ? c.acertos + '/' + c.validas + ' (nota ' + c.nota + ')' : 'aguardando gabarito') +
            '\n\nPlanilha: ' + planilha_().getUrl()
    });
  } catch (e) {
    console.error('Não foi possível enviar o e-mail: ' + e.message);
  }
}
