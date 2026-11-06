# Como instalar o site de simulados numa escola

Cada escola tem **o seu próprio banco** (no Supabase, onde ficam os simulados, as imagens e as
respostas dos alunos) e **o seu próprio endereço** do site (no Cloudflare Pages). O código do site é
o mesmo para todas: quando ele é atualizado no GitHub, o site de todas as escolas se atualiza sozinho.

Tempo: uns 30 minutos. Tudo é grátis. Você vai precisar de:

- o e-mail **da escola** (institucional), para a conta do Supabase. Os dados dos alunos ficam nele (LGPD).
- o nome e o e-mail de quem vai ser o **administrador** da escola (coordenação, por exemplo).
- acesso à conta do Cloudflare e do GitHub de quem cuida do site.

---

## Parte 1 — O banco da escola (Supabase)

1. Entre em **https://supabase.com** e crie uma conta com o **e-mail da escola** (botão *Start your project*).
2. Clique em **New project**:
   - **Project name:** `simulados-<nome da escola>` (ex.: `simulados-escola-x`);
   - **Database password:** clique em *Generate a password*. Não é preciso guardar essa senha: o site não usa.
   - **Region:** *South America (São Paulo)*: fica mais rápido para os alunos;
   - clique em **Create new project** e espere uns 2 minutos até ficar pronto.
3. Monte o banco:
   - no menu da esquerda, clique em **SQL Editor**;
   - abra o arquivo [supabase/esquema.sql](supabase/esquema.sql) deste repositório, copie **tudo** e cole na caixa;
   - clique em **Run**. Deve aparecer *Success. No rows returned*.
4. **Feche o cadastro livre** (importante para a segurança):
   - menu **Authentication** → **Sign In / Providers**;
   - desligue **Allow new users to sign up** e clique em **Save changes** (leva até 1 minuto para valer).

   Sem isso, qualquer pessoa poderia criar uma conta no banco da escola. Os professores são
   cadastrados pelo administrador, dentro do site.
5. Crie a conta do administrador:
   - menu **Authentication** → **Users** → **Add user** → **Create new user**;
   - **Email:** o e-mail do administrador; **Password:** uma senha provisória (anote);
   - deixe marcado **Auto Confirm User** e clique em **Create user**.
6. Copie os dois dados que o site precisa (cole num bloco de notas por enquanto):
   - **Endereço do banco:** menu **Project Settings** → **Data API** → *Project URL*
     (parece com `https://abcd1234.supabase.co`);
   - **Chave pública:** **Project Settings** → **API Keys** → *Publishable key*
     (começa com `sb_publishable_`).

   > ⚠️ **Nunca** use a *Secret key* (`sb_secret_...`) nem a *service_role*. Elas abrem tudo e não
   > podem ir para o site. O site recusa essas chaves.

---

## Parte 2 — O site da escola (Cloudflare Pages)

1. Entre em **https://dash.cloudflare.com** (conta de quem cuida do site).
2. Menu **Workers & Pages** → **Create** → aba **Pages** → **Import an existing Git repository**.
   Se aparecer só *Workers*, procure o link *Looking to deploy Pages? Get started*.
3. Escolha o repositório **pdf-para-simulado** e clique em **Begin setup**.
   Na primeira vez, o Cloudflare pede permissão para ler o GitHub: autorize só este repositório.
4. Preencha:
   - **Project name:** vira o endereço do site. `simulados-escola-x` → `https://simulados-escola-x.pages.dev`;
   - **Production branch:** `main`;
   - **Framework preset:** `None`;
   - **Build command:** `node gerar_config.js`;
   - **Build output directory:** `public`;
   - **Root directory (advanced):** `web`;
   - **Environment variables** (clique em *Add variable* duas vezes):
     - `SUPABASE_URL` = o endereço do banco (Parte 1, passo 6);
     - `SUPABASE_KEY` = a chave pública (Parte 1, passo 6).
5. Clique em **Save and Deploy** e espere terminar (1 a 2 minutos). Clique em **Continue to project**
   e abra o endereço `.pages.dev`.

Se a construção falhar com *SUPABASE_URL ausente ou inválida* ou *chave SECRETA*, corrija a variável
em **Settings** → **Variables and Secrets** e clique em **Retry deployment** (aba *Deployments*).

---

## Parte 3 — Primeiro acesso do administrador

1. Abra `https://<endereço da escola>.pages.dev` e clique em **Sou professor**.
2. Entre com o e-mail e a senha provisória da Parte 1, passo 5.
3. Aparece **Primeiro acesso**: digite o nome e clique para virar o administrador.
   (Isso só funciona uma vez: depois que a escola tem um administrador, ninguém mais consegue.)
4. Troque a senha em **Trocar minha senha**.
5. Botão **👥 Professores** → cadastre cada professor. O site gera uma senha provisória e mostra um
   texto pronto para enviar a ele (botão *Copiar para enviar ao professor*). No primeiro acesso, o
   professor é obrigado a criar a própria senha.

Os alunos não têm conta: entram pelo endereço da escola → **Sou aluno** → digitam o código que o
professor mostrar na sala (ou abrem o link que o professor mandar).

---

## Parte 4 — Despertador (para o banco não "dormir" nas férias)

O Supabase grátis **pausa** o banco depois de uns 7 dias sem nenhum acesso. A tarefa
[.github/workflows/despertador.yml](.github/workflows/despertador.yml) acessa o banco de todas as
escolas 2 vezes por semana.

1. No GitHub, abra o repositório → **Settings** → **Secrets and variables** → **Actions**.
2. Se o segredo **ESCOLAS** ainda não existe, clique em **New repository secret** e dê o nome `ESCOLAS`.
   Se já existe, clique no lápis dele (o GitHub não mostra o valor antigo: guarde a lista completa
   num lugar seguro, fora do repositório, e cole a lista inteira de novo).
3. O valor é uma linha por escola: o endereço do banco, um espaço, a chave pública. Exemplo:

   ```
   https://abcd1234.supabase.co sb_publishable_xxxxxxxx
   https://efgh5678.supabase.co sb_publishable_yyyyyyyy
   ```
4. Teste: aba **Actions** → **Despertador do Supabase** → **Run workflow**. Deve terminar em verde,
   com "Escola N: acordada" para cada escola.

Se uma escola não responder, a tarefa fica vermelha e o GitHub manda e-mail: entre no Supabase dessa
escola e clique em **Restore project**. O GitHub também desliga tarefas agendadas depois de 60 dias
sem mudança no repositório. Ele avisa por e-mail antes, e basta clicar em **Enable workflow** na aba
*Actions*.

---

## Quando o código for atualizado

- **Páginas do site** (pasta `web/`): nada a fazer. O Cloudflare publica sozinho em todas as escolas.
- **Banco** (arquivo `supabase/esquema.sql` mudou): em cada escola, abra o **SQL Editor**, cole o
  `esquema.sql` novo inteiro e clique em **Run**. Ele pode ser rodado de novo sem perder dados.

## Limites do plano grátis (por escola)

| O quê | Limite | Na prática |
|---|---|---|
| Imagens das questões | 1 GB | ~130 provas. Apague simulados antigos (o botão apaga as imagens junto) |
| Download pelos alunos | ~5 GB por mês | ~15 simulados de 30 questões com 100 alunos cada |
| Banco (respostas) | 500 MB | sobra muito: cada envio ocupa menos de 1 KB |
| Pausa | depois de 7 dias sem acesso | o despertador (Parte 4) evita |

Para ver o uso: no Supabase, **Organization** → **Usage**.

## Problemas comuns

- **"E-mail ou senha incorretos"** no primeiro acesso: confira se marcou *Auto Confirm User* (Parte 1,
  passo 5). Se não marcou, apague o usuário e crie de novo.
- **O site abre, mas não lista nada / dá erro de conexão:** o banco pode estar pausado (Restore
  project) ou a variável `SUPABASE_URL`/`SUPABASE_KEY` está errada (Parte 2).
- **O administrador esqueceu a senha:** outro administrador gera uma nova em **Professores**. Se não
  houver outro, no Supabase da escola abra o **SQL Editor**, cole o texto abaixo (troque o e-mail e a
  senha provisória) e clique em **Run**. No próximo acesso, ele cria uma senha nova.

  ```sql
  update auth.users set encrypted_password = extensions.crypt('SenhaProvisoria123', extensions.gen_salt('bf')),
         banned_until = null, updated_at = now()
   where email = 'admin@escola.com.br';
  update professores set trocar_senha = true, ativo = true where email = 'admin@escola.com.br';
  ```

  Não apague o usuário: os simulados dele seriam apagados junto.
