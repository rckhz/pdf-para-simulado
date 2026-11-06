// Roda na hospedagem (Cloudflare Pages) ao publicar o site: monta a pasta public/ com as páginas
// e o config.js, que diz o endereço do banco da escola (Supabase) e a chave PÚBLICA dele.
// Os dois vêm das variáveis da hospedagem (Settings > Environment variables):
//   SUPABASE_URL = https://<projeto>.supabase.co
//   SUPABASE_KEY = a chave pública (sb_publishable_... ou a "anon" antiga)
// NUNCA use aqui a chave secreta (service role / sb_secret_...): ela daria acesso a tudo.
const fs = require('fs');
const path = require('path');

const url = String(process.env.SUPABASE_URL || '').trim().replace(/\/+$/, '');
const chave = String(process.env.SUPABASE_KEY || '').trim();
if (!/^https:\/\/[a-z0-9]+\.supabase\.co$/.test(url)) {
  console.error('SUPABASE_URL ausente ou inválida (ex.: https://abcd1234.supabase.co).');
  process.exit(1);
}
if (/^sb_secret_/.test(chave) || /service_role/.test(Buffer.from((chave.split('.')[1] || ''), 'base64').toString())) {
  console.error('SUPABASE_KEY é a chave SECRETA. Use a chave pública (publishable/anon).');
  process.exit(1);
}
if (!chave) {
  console.error('SUPABASE_KEY ausente.');
  process.exit(1);
}

const saida = path.join(__dirname, 'public');
fs.mkdirSync(saida, { recursive: true });
for (const arquivo of fs.readdirSync(__dirname)) {
  if (/\.(html|css|js|png|svg|ico|webp)$/.test(arquivo) && !['gerar_config.js', 'config.js'].includes(arquivo)) {
    fs.copyFileSync(path.join(__dirname, arquivo), path.join(saida, arquivo));
  }
}
fs.writeFileSync(path.join(saida, 'config.js'),
  'window.SUPABASE_URL = ' + JSON.stringify(url) + ';\nwindow.SUPABASE_KEY = ' + JSON.stringify(chave) + ';\n');
console.log('Site pronto em public/.');
