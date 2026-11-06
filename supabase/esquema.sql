-- =====================================================================
-- pdf-para-simulado · banco do SITE PRÓPRIO (Supabase)
--
-- Como usar: no painel do Supabase, abra "SQL Editor", cole este arquivo inteiro e clique em
-- "Run". Pode rodar de novo quando ele mudar: ele só cria o que falta e atualiza as funções
-- (não apaga dados).
--
-- Resumo (veja PLANO_SUPABASE.md):
--   * Tabelas: config_escola, professores, simulados, imagens_questao, gabaritos, envios.
--   * O ALUNO (sem login, papel "anon") NÃO lê nenhuma tabela. Ele só chama 4 funções:
--       provas_abertas(), prova_por_codigo(), dados_prova(), enviar_respostas().
--     O gabarito nunca sai do banco: a correção é feita aqui dentro.
--   * O PROFESSOR (logado, papel "authenticated") só vê e mexe nos simulados dele;
--     o ADMINISTRADOR vê tudo da escola. Regras: Row Level Security (RLS).
--   * Imagens das questões: pasta (bucket) "questoes" no Storage, uma subpasta por simulado
--     (o nome da subpasta é o id do simulado). A pasta é pública para LEITURA (o aluno baixa as
--     imagens pelo endereço, que tem o id do simulado, impossível de adivinhar); só o professor
--     dono grava ou apaga.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. TABELAS
-- ---------------------------------------------------------------------

-- Configuração da escola (uma linha só).
create table if not exists public.config_escola (
  id            int primary key default 1 check (id = 1),
  nome          text not null default 'Minha escola',
  mostrar_certa boolean not null default true   -- aluno vê a letra certa depois de enviar (padrão: sim)
);
insert into public.config_escola (id) values (1) on conflict (id) do nothing;

-- Professores e administrador (cada um é um usuário do login do Supabase).
create table if not exists public.professores (
  id        uuid primary key references auth.users (id) on delete cascade,
  nome      text not null default '',
  email     text not null default '',
  papel     text not null default 'professor' check (papel in ('admin', 'professor')),
  ativo     boolean not null default true,
  criado_em timestamptz not null default now()
);
-- (colunas acrescentadas depois: "add column if not exists" para quem já rodou a versão anterior)
alter table public.professores add column if not exists trocar_senha boolean not null default false;   -- senha provisória

create table if not exists public.simulados (
  id               uuid primary key default gen_random_uuid(),
  professor_id     uuid not null references public.professores (id) on delete cascade,
  titulo           text not null,
  total_questoes   int not null check (total_questoes between 1 and 300),
  primeira         int not null default 1 check (primeira >= 1),   -- numeração inicial (ENEM 2º dia: 91)
  num_alternativas int[] not null default '{}',                    -- por questão: 4 (A-D) ou 5 (A-E)
  materias         text[] not null default '{}',                   -- por questão
  avisos           jsonb not null default '{}',                    -- avisos do leitor de provas
  status           text not null default 'rascunho' check (status in ('rascunho', 'aberta', 'encerrada')),
  codigo_atual     text unique,                                    -- código que o aluno digita (ex.: K7P2QX)
  codigo_expira_em timestamptz,                                    -- fecho automático da prova
  aparece_na_lista boolean not null default true,                  -- aparece na lista de provas abertas?
  criado_em        timestamptz not null default now()
);
create index if not exists simulados_professor on public.simulados (professor_id);

create table if not exists public.imagens_questao (
  simulado_id uuid not null references public.simulados (id) on delete cascade,
  numero      int not null check (numero >= 1),
  caminho     text not null,                                       -- caminho dentro do bucket "questoes"
  primary key (simulado_id, numero)
);

-- Gabarito: letras A-E, '' (falta) ou 'X' (questão anulada). TRANCADO: o aluno nunca lê.
create table if not exists public.gabaritos (
  simulado_id uuid primary key references public.simulados (id) on delete cascade,
  respostas   text[] not null default '{}'
);

create table if not exists public.envios (
  id          bigint generated always as identity primary key,
  simulado_id uuid not null references public.simulados (id) on delete cascade,
  nome        text not null,
  turma       text not null,
  respostas   text[] not null,
  acertos     int,                 -- vazio enquanto o gabarito não estiver completo
  validas     int,                 -- questões que contam (sem as anuladas)
  nota        numeric(4, 1),
  corrigido   boolean not null default false,
  quando      timestamptz not null default now()
);
create index if not exists envios_simulado on public.envios (simulado_id, quando);


-- ---------------------------------------------------------------------
-- 2. FUNÇÕES DE APOIO (quem é quem)
-- ---------------------------------------------------------------------

-- Papel de quem está logado ('admin', 'professor') ou nulo (aluno, desconhecido ou desativado).
create or replace function public.meu_papel() returns text
language sql stable security definer set search_path = public as $$
  select papel from professores where id = auth.uid() and ativo
$$;

create or replace function public.eh_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(meu_papel() = 'admin', false)
$$;

-- O simulado é de quem está logado (ou quem está logado é o administrador)?
create or replace function public.dono_do_simulado(sid uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from simulados s
    where s.id = sid
      and ((s.professor_id = auth.uid() and meu_papel() is not null) or eh_admin())
  )
$$;

-- Igual, mas recebendo o nome de uma pasta do Storage (texto que deveria ser o id do simulado).
create or replace function public.dono_da_pasta(pasta text) returns boolean
language plpgsql stable security definer set search_path = public as $$
begin
  return dono_do_simulado(pasta::uuid);
exception when invalid_text_representation then
  return false;
end
$$;


-- ---------------------------------------------------------------------
-- 3. CORREÇÃO (a mesma regra do app atual)
--    nota = acertos / válidas × 10 (1 casa); em branco conta como errada; 'X' = anulada.
-- ---------------------------------------------------------------------
create or replace function public.corrigir(gab text[], resp text[], total int)
returns table (completo boolean, acertos int, validas int, nota numeric, correcao text[])
language plpgsql immutable set search_path = public as $$
declare
  i int; g text; r text;
  c text[] := '{}'; a int := 0; v int := 0;
begin
  completo := coalesce(array_length(gab, 1), 0) >= total and not exists (
    select 1 from generate_series(1, total) k where coalesce(gab[k], '') not in ('A', 'B', 'C', 'D', 'E', 'X'));
  if not completo then
    acertos := null; validas := null; nota := null; correcao := '{}';
    return next;
    return;
  end if;
  for i in 1 .. total loop
    g := gab[i];
    r := coalesce(resp[i], '');
    if g = 'X' then
      c := array_append(c, 'anulada');
    else
      v := v + 1;
      if r = '' then
        c := array_append(c, 'branco');
      elsif r = g then
        c := array_append(c, 'certa');
        a := a + 1;
      else
        c := array_append(c, 'errada');
      end if;
    end if;
  end loop;
  acertos := a;
  validas := v;
  nota := case when v > 0 then round(a::numeric / v * 10, 1) else 0 end;
  correcao := c;
  return next;
end
$$;

-- Quando o gabarito muda, as notas de quem já respondeu são recalculadas (como no app atual).
create or replace function public.recorrigir_envios() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  tot int; e record; k record;
begin
  select total_questoes into tot from simulados where id = new.simulado_id;
  for e in select id, respostas from envios where simulado_id = new.simulado_id loop
    select * into k from corrigir(new.respostas, e.respostas, tot);
    update envios
       set acertos = k.acertos, validas = k.validas, nota = k.nota, corrigido = k.completo
     where id = e.id;
  end loop;
  return new;
end
$$;
drop trigger if exists gabarito_mudou on public.gabaritos;
create trigger gabarito_mudou after insert or update of respostas on public.gabaritos
  for each row execute function public.recorrigir_envios();


-- ---------------------------------------------------------------------
-- 4. FUNÇÕES DO ALUNO (sem login)
-- ---------------------------------------------------------------------

-- Provas abertas que o professor deixou aparecer na lista.
create or replace function public.provas_abertas()
returns table (id uuid, nome text, total int)
language sql stable security definer set search_path = public as $$
  select s.id, s.titulo, s.total_questoes
    from simulados s
   where s.status = 'aberta' and s.aparece_na_lista
     and (s.codigo_expira_em is null or s.codigo_expira_em > now())
   order by s.criado_em desc
$$;

-- O aluno digitou um código: devolve o id da prova (se estiver aberta).
create or replace function public.prova_por_codigo(codigo text) returns uuid
language plpgsql stable security definer set search_path = public as $$
declare
  limpo text := upper(regexp_replace(coalesce(codigo, ''), '[^A-Za-z0-9]', '', 'g'));
  sid uuid;
begin
  select s.id into sid
    from simulados s
   where s.codigo_atual = limpo and s.status = 'aberta'
     and (s.codigo_expira_em is null or s.codigo_expira_em > now());
  if sid is null then
    raise exception 'Código inválido ou prova encerrada. Confira o código com o professor.';
  end if;
  return sid;
end
$$;

-- Tudo o que a página do aluno precisa (NÃO inclui o gabarito).
-- Os nomes dos campos são os mesmos do app atual (nome, total, materias, alternativas, primeira).
create or replace function public.dados_prova(prova_id uuid) returns json
language plpgsql stable security definer set search_path = public as $$
declare
  s simulados;
begin
  select * into s from simulados where id = prova_id;
  if not found then
    raise exception 'Simulado não encontrado.';
  end if;
  if s.status <> 'aberta' or (s.codigo_expira_em is not null and s.codigo_expira_em <= now()) then
    raise exception 'Este simulado não está aberto no momento.';
  end if;
  return json_build_object(
    'id', s.id, 'nome', s.titulo, 'total', s.total_questoes, 'primeira', s.primeira,
    'materias', s.materias, 'alternativas', s.num_alternativas,
    'imagens', (select coalesce(json_agg(i.caminho order by i.numero), '[]'::json)
                  from imagens_questao i where i.simulado_id = s.id));
end
$$;

-- O aluno envia as respostas: confere, corrige aqui dentro, grava e devolve só o resultado.
-- O resultado tem o mesmo formato do app atual (corrigido, acertos, erros, total, anuladas, nota,
-- correcao, certas). "certas" (a letra certa de cada questão) só vai se a escola deixar.
create or replace function public.enviar_respostas(prova_id uuid, nome_aluno text, turma_aluno text, marcadas text[])
returns json
language plpgsql volatile security definer set search_path = public as $$
declare
  s simulados;
  resp text[] := '{}';
  r text; lim int; i int;
  gab text[]; k record; mostrar boolean;
begin
  nome_aluno := btrim(coalesce(nome_aluno, ''));
  turma_aluno := btrim(coalesce(turma_aluno, ''));
  if nome_aluno = '' or turma_aluno = '' then
    raise exception 'Preencha nome e turma.';
  end if;
  if length(nome_aluno) > 120 or length(turma_aluno) > 40 then
    raise exception 'Nome ou turma muito longos.';
  end if;

  select * into s from simulados where id = prova_id;
  if not found then
    raise exception 'Simulado não encontrado.';
  end if;
  if s.status <> 'aberta' or (s.codigo_expira_em is not null and s.codigo_expira_em <= now()) then
    raise exception 'Este simulado não está mais aberto.';
  end if;

  if coalesce(array_length(marcadas, 1), 0) > s.total_questoes then
    raise exception 'Há mais respostas do que questões.';
  end if;
  for i in 1 .. s.total_questoes loop
    r := upper(btrim(coalesce(marcadas[i], '')));
    lim := coalesce(s.num_alternativas[i], 5);
    if lim not in (4, 5) then lim := 5; end if;
    if r <> '' and (length(r) <> 1 or position(r in left('ABCDE', lim)) = 0) then
      raise exception 'Alternativa inválida na questão %.', i;
    end if;
    resp := array_append(resp, r);   -- em branco = '' (conta como errada)
  end loop;

  -- Limites contra abuso (o aluno não tem login).
  if (select count(*) from envios e
       where e.simulado_id = prova_id and e.quando > now() - interval '1 minute') >= 300 then
    raise exception 'Muitos envios ao mesmo tempo. Tente de novo em 1 minuto.';
  end if;
  if (select count(*) from envios e
       where e.simulado_id = prova_id and lower(e.nome) = lower(nome_aluno) and lower(e.turma) = lower(turma_aluno)
         and e.quando > now() - interval '10 minutes') >= 10 then
    raise exception 'Muitos envios seguidos com este nome. Espere alguns minutos.';
  end if;

  select g.respostas into gab from gabaritos g where g.simulado_id = prova_id;
  select * into k from corrigir(coalesce(gab, '{}'), resp, s.total_questoes);
  insert into envios (simulado_id, nome, turma, respostas, acertos, validas, nota, corrigido)
  values (prova_id, nome_aluno, turma_aluno, resp, k.acertos, k.validas, k.nota, k.completo);

  select c.mostrar_certa into mostrar from config_escola c where c.id = 1;
  return json_build_object(
    'corrigido', k.completo,
    'acertos', k.acertos,
    'erros', k.validas - k.acertos,
    'total', coalesce(k.validas, s.total_questoes),
    'anuladas', case when k.completo then s.total_questoes - k.validas else 0 end,
    'nota', k.nota,
    'correcao', coalesce(k.correcao, '{}'),
    'certas', case when k.completo and coalesce(mostrar, true) then gab[1:s.total_questoes] else '{}'::text[] end);
end
$$;


-- ---------------------------------------------------------------------
-- 5. FUNÇÕES DO PROFESSOR / ADMINISTRADOR (logado)
-- ---------------------------------------------------------------------

-- Abre a prova para os alunos e gera um código novo (o anterior deixa de valer).
-- "horas": fecho automático de segurança (padrão 4 h; de 1 a 72).
create or replace function public.abrir_prova(prova_id uuid, horas int default 4) returns json
language plpgsql volatile security definer set search_path = public as $$
declare
  s simulados;
  alfabeto text := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   -- sem 0/O e 1/I (que se confundem)
  novo text; b bytea; i int;
begin
  if not dono_do_simulado(prova_id) then
    raise exception 'Simulado não encontrado.';
  end if;
  if horas is null or horas < 1 or horas > 72 then
    raise exception 'Duração inválida: escolha de 1 a 72 horas.';
  end if;
  select * into s from simulados where id = prova_id;
  if (select count(*) from imagens_questao where simulado_id = prova_id) < s.total_questoes then
    raise exception 'Faltam imagens de questões. Envie o PDF da prova de novo.';
  end if;
  loop
    b := extensions.gen_random_bytes(6);
    novo := '';
    for i in 0 .. 5 loop
      novo := novo || substr(alfabeto, (get_byte(b, i) % 32) + 1, 1);
    end loop;
    exit when not exists (select 1 from simulados where codigo_atual = novo);
  end loop;
  update simulados
     set status = 'aberta', codigo_atual = novo, codigo_expira_em = now() + make_interval(hours => horas)
   where id = prova_id;
  return json_build_object('codigo', novo, 'expira_em', now() + make_interval(hours => horas));
end
$$;

create or replace function public.encerrar_prova(prova_id uuid) returns void
language plpgsql volatile security definer set search_path = public as $$
begin
  if not dono_do_simulado(prova_id) then
    raise exception 'Simulado não encontrado.';
  end if;
  update simulados set status = 'encerrada', codigo_atual = null, codigo_expira_em = null where id = prova_id;
end
$$;

-- A escola já tem administrador? (a tela de login usa para mostrar o "primeiro acesso")
create or replace function public.escola_tem_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from professores where papel = 'admin')
$$;

-- O professor trocou a senha provisória (o login já trocou a senha; aqui só tira o aviso).
create or replace function public.senha_trocada() returns void
language sql volatile security definer set search_path = public as $$
  update professores set trocar_senha = false where id = auth.uid()
$$;

-- Instalação: a 1ª pessoa logada vira o administrador (só funciona enquanto não houver nenhum).
create or replace function public.assumir_administracao(nome_admin text) returns void
language plpgsql volatile security definer set search_path = public as $$
begin
  if auth.uid() is null then
    raise exception 'Faça login primeiro.';
  end if;
  lock table professores in exclusive mode;
  if exists (select 1 from professores where papel = 'admin') then
    raise exception 'Esta escola já tem um administrador.';
  end if;
  insert into professores (id, nome, email, papel)
  select u.id, coalesce(nullif(btrim(nome_admin), ''), 'Administrador'), coalesce(u.email, ''), 'admin'
    from auth.users u where u.id = auth.uid();
end
$$;


-- ---------------------------------------------------------------------
-- 6. REGRAS DE ACESSO (Row Level Security)
-- ---------------------------------------------------------------------
alter table public.config_escola   enable row level security;
alter table public.professores     enable row level security;
alter table public.simulados       enable row level security;
alter table public.imagens_questao enable row level security;
alter table public.gabaritos       enable row level security;
alter table public.envios          enable row level security;

drop policy if exists config_ler on public.config_escola;
create policy config_ler on public.config_escola for select to authenticated using (meu_papel() is not null);
drop policy if exists config_alterar on public.config_escola;
create policy config_alterar on public.config_escola for update to authenticated using (eh_admin()) with check (eh_admin());

drop policy if exists professores_ler on public.professores;
create policy professores_ler on public.professores for select to authenticated using (id = auth.uid() or eh_admin());
drop policy if exists professores_alterar on public.professores;
create policy professores_alterar on public.professores for update to authenticated using (eh_admin()) with check (eh_admin());
-- (professores são criados pela função do servidor "criar professor", etapa 4)

-- (as regras da própria tabela olham o dono NA LINHA: consultar a tabela de novo não enxergaria
--  uma prova que acabou de ser criada no mesmo comando)
drop policy if exists simulados_ler on public.simulados;
create policy simulados_ler on public.simulados for select to authenticated
  using ((professor_id = auth.uid() and meu_papel() is not null) or eh_admin());
drop policy if exists simulados_criar on public.simulados;
create policy simulados_criar on public.simulados for insert to authenticated
  with check (professor_id = auth.uid() and meu_papel() is not null);
drop policy if exists simulados_alterar on public.simulados;
create policy simulados_alterar on public.simulados for update to authenticated
  using ((professor_id = auth.uid() and meu_papel() is not null) or eh_admin())
  with check ((professor_id = auth.uid() and meu_papel() is not null) or eh_admin());
drop policy if exists simulados_apagar on public.simulados;
create policy simulados_apagar on public.simulados for delete to authenticated
  using ((professor_id = auth.uid() and meu_papel() is not null) or eh_admin());

drop policy if exists imagens_dono on public.imagens_questao;
create policy imagens_dono on public.imagens_questao for all to authenticated
  using (dono_do_simulado(simulado_id)) with check (dono_do_simulado(simulado_id));

drop policy if exists gabaritos_dono on public.gabaritos;
create policy gabaritos_dono on public.gabaritos for all to authenticated
  using (dono_do_simulado(simulado_id)) with check (dono_do_simulado(simulado_id));

-- envios: o professor lê e apaga; gravar, só pela função enviar_respostas (aluno).
drop policy if exists envios_ler on public.envios;
create policy envios_ler on public.envios for select to authenticated using (dono_do_simulado(simulado_id));
drop policy if exists envios_apagar on public.envios;
create policy envios_apagar on public.envios for delete to authenticated using (dono_do_simulado(simulado_id));


-- ---------------------------------------------------------------------
-- 7. PERMISSÕES (quem pode chamar o quê)
-- ---------------------------------------------------------------------
-- O aluno (anon) não toca em tabela nenhuma.
revoke all on public.config_escola, public.professores, public.simulados, public.imagens_questao,
              public.gabaritos, public.envios from anon;
grant select, insert, update, delete on public.config_escola, public.professores, public.simulados,
      public.imagens_questao, public.gabaritos, public.envios to authenticated;   -- o RLS filtra

revoke execute on all functions in schema public from public, anon, authenticated;
grant execute on function public.provas_abertas(), public.prova_por_codigo(text), public.dados_prova(uuid),
      public.enviar_respostas(uuid, text, text, text[]), public.escola_tem_admin() to anon, authenticated;
grant execute on function public.meu_papel(), public.eh_admin(), public.dono_do_simulado(uuid),
      public.dono_da_pasta(text), public.abrir_prova(uuid, int), public.encerrar_prova(uuid),
      public.assumir_administracao(text), public.senha_trocada() to authenticated;


-- ---------------------------------------------------------------------
-- 8. IMAGENS DAS QUESTÕES (Storage)
-- ---------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('questoes', 'questoes', true, 2097152, array['image/webp', 'image/jpeg', 'image/png'])
on conflict (id) do update
  set public = excluded.public, file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "questoes: professor grava" on storage.objects;
create policy "questoes: professor grava" on storage.objects for insert to authenticated
  with check (bucket_id = 'questoes' and public.dono_da_pasta((storage.foldername(name))[1]));
drop policy if exists "questoes: professor troca" on storage.objects;
create policy "questoes: professor troca" on storage.objects for update to authenticated
  using (bucket_id = 'questoes' and public.dono_da_pasta((storage.foldername(name))[1]));
drop policy if exists "questoes: professor lista" on storage.objects;   -- (o Storage exige isto para apagar)
create policy "questoes: professor lista" on storage.objects for select to authenticated
  using (bucket_id = 'questoes' and public.dono_da_pasta((storage.foldername(name))[1]));
drop policy if exists "questoes: professor apaga" on storage.objects;
create policy "questoes: professor apaga" on storage.objects for delete to authenticated
  using (bucket_id = 'questoes' and public.dono_da_pasta((storage.foldername(name))[1]));
