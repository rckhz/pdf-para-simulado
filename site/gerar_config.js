// Roda na Vercel ao publicar o site (veja vercel.json): monta a pasta public/ com a página
// e o config.js, que diz o endereço do app no Google Apps Script.
// O endereço vem da variável APP_URL, configurada na Vercel (Settings > Environment Variables):
//   https://script.google.com/macros/s/<código da implantação>/exec
// Assim o endereço do app não fica gravado no GitHub.
const fs = require('fs');
const path = require('path');

const url = String(process.env.APP_URL || '').trim().replace(/\/u\/\d+\//, '/');   // tira um ".../u/1/..." colado sem querer
if (!/^https:\/\/script\.google\.com\/macros\/s\/[\w-]+\/exec$/.test(url)) {
  console.error('APP_URL ausente ou inválida. Use o link do app terminado em /exec, sem "?professor".');
  process.exit(1);
}
const saida = path.join(__dirname, 'public');
fs.mkdirSync(saida, { recursive: true });
fs.copyFileSync(path.join(__dirname, 'index.html'), path.join(saida, 'index.html'));
fs.writeFileSync(path.join(saida, 'config.js'), 'window.APP_URL = ' + JSON.stringify(url) + ';\n');
console.log('Site pronto em public/ (APP_URL configurada).');
