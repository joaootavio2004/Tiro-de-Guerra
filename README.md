# 🪖 Tiro de Guerra — Bot do Telegram

Sistema de inscrições e resultados do **GUERRA Clube de Tiro**.
A recepção e os ROs operam tudo pelo Telegram (conversa privada com o bot),
e os resultados ficam guardados para o site (próxima fase).

Este guia foi escrito passo a passo, **sem exigir conhecimento de programação**.

---

## Como funciona (visão geral)

- **Recepção / RO** inscrevem o atirador numa etapa, escolhendo modalidade
  (pistola/carabina) e a quantidade de inscrições.
- **RO** clica no atirador na "linha de tiro", digita o tempo e marca
  penalidades (2, 5, 10 ou DQ). O sistema calcula os pontos na hora.
- A **classificação** da etapa e do mês fica disponível no bot (e, em breve,
  no site).
- No fim do mês, o **administrador** fecha o mês e o bot mostra a proposta de
  subidas e descidas de categoria para você confirmar.

A pontuação é idêntica à da sua planilha antiga:
o menor tempo de cada categoria vale **100 pontos** e os demais recebem
`(menor tempo ÷ seu tempo) × 100`. No mês, soma-se as **3 melhores etapas**.
Penalidades são segundos somados ao tempo; DQ zera a etapa.

> O histórico de fevereiro a junho/2026 da sua planilha **já vem carregado**.

---

## Passo 1 — Criar o bot no Telegram

1. No Telegram, procure por **@BotFather** e abra a conversa.
2. Envie `/newbot`.
3. Escolha um **nome** (ex.: `Guerra Clube de Tiro`) e um **usuário** que
   termine em `bot` (ex.: `guerra_tiro_bot`).
4. O BotFather vai te enviar um **TOKEN** (algo como
   `8123456789:AAH...`). **Guarde esse token** — ele é a senha do bot.
   ⚠️ Nunca compartilhe publicamente.

> Dica: ainda no BotFather, envie `/setprivacy` → escolha seu bot →
> **Disable** (não é obrigatório, mas evita surpresas se um dia for usado em grupo).

---

## Passo 2 — Descobrir o seu ID do Telegram

Você precisa do seu **ID numérico** para ser o administrador.

- Abra o bot **@userinfobot** no Telegram e envie qualquer mensagem.
  Ele responde com o seu `Id` (um número como `123456789`).

Se houver mais de um administrador, anote o ID de cada um.

---

## Passo 3 — Publicar no Coolify

No painel do seu Coolify:

1. **+ New Resource** → escolha **Docker Compose** (ou "Application" via
   Dockerfile — ambos funcionam). Aponte para o repositório onde você subiu
   estes arquivos (ou faça upload do projeto).
2. Em **Environment Variables**, adicione:
   - `BOT_TOKEN` = o token do Passo 1
   - `ADMIN_IDS` = seu ID do Passo 2 (vários separados por vírgula:
     `123456789,987654321`)
   - `CLUB_NAME` = `GUERRA Clube de Tiro` (opcional)
3. **Volume persistente** (importante, para não perder os dados):
   - O `docker-compose.yml` já cria um volume chamado `tiroguerra_data`
     apontando para `/app/data`. O Coolify reconhece isso automaticamente.
4. **Domínio**: associe seu domínio/subdomínio ao serviço (porta **8000**).
   Por enquanto ele mostra uma página simples; o site de resultados entra
   na próxima fase, no mesmo endereço.
5. Clique em **Deploy**.

Pronto. Em ~1 minuto o bot está no ar. No primeiro start, o histórico da
planilha é carregado sozinho.

> **Quer começar do zero (sem o histórico)?** Apague a pasta `seed/` antes
> de implantar. Aí o sistema sobe vazio, só com as categorias padrão.

---

## Passo 4 — Primeiro acesso

1. No Telegram, abra **seu bot** e envie `/start`.
   Como seu ID está em `ADMIN_IDS`, você já entra como **Administrador**.
2. Vá em **⚙️ Administração ▸ 📅 Etapas/Mês ▸ ➕ Abrir nova etapa**.
   Isso cria a 1ª etapa do mês e libera as inscrições.
3. Peça para a recepção e os ROs abrirem o bot e enviarem `/start`.
   Eles tocam em **🙋 Pedir acesso** e você recebe uma notificação para
   aprovar com um toque, escolhendo **Recepção** ou **RO**.

---

## Guia rápido de uso

**Recepção (inscrever):**
`📝 Inscrever atirador` → modalidade → buscar nome (ou ➕ novo) →
quantidade de inscrições → confirmar.

**RO (lançar resultado):**
`🎯 Lançar resultado` → toca no atirador → digita o tempo →
ajusta penalidades com ➕/➖ (ou marca DQ) → **✅ Salvar**.
Reinscrição/várias corridas: pode lançar de novo, **vale sempre a melhor**.

**Ver inscritos / classificação:**
`📋 Inscritos da etapa` e `🏆 Classificação` (por categoria, do mês).

**Fechar o mês (admin):**
`⚙️ Administração ▸ 📅 Etapas/Mês ▸ 🏁 Fechar mês`. O bot mostra quem sobe e
quem desce; você confere e confirma. As categorias são atualizadas e um novo
mês começa.

Comandos úteis: `/start` (menu), `/id` (mostra seu ID), `/cancel` (cancela).

---

## Estrutura do projeto (para referência)

```
app/            código do sistema
  scoring.py    cálculo dos pontos (validado contra a planilha)
  db.py         banco de dados (SQLite)
  standings.py  classificações (etapa e mês)
  bot.py        os fluxos do Telegram
  texts.py      textos em português
scripts/
  import_planilha.py   importa a planilha antiga
seed/
  tiro_guerra.db       histórico já carregado (fev–jun/2026)
run.py          inicia o bot + servidor web juntos
Dockerfile / docker-compose.yml   implantação no Coolify
.env.example    modelo das variáveis de ambiente
```

---

## Próxima fase: o site

O sistema já guarda tudo pronto para o site de resultados, que vai usar as
cores da marca (verde/dourado da logo) e mostrar as classificações ao público
no seu domínio — sem precisar mexer nos dados do bot.
