# ATUALIZAÇÃO — Tiro de Guerra · Área interna (/interno)

## O que muda

O site ganha uma **área logada** em `tirodeguerra.com.br/interno` com tudo que o
bot faz — e mais:

- **Linha de tiro** com **timer Special Pie por Bluetooth** (start/stop pelo
  celular, disparos lidos ao vivo) e **impressora térmica** (cupom por passada).
  Funções da passada: **Reiniciar** (descarta e dispara o timer de novo),
  **Somar** (pane na pista: guarda o tempo corrido e continua no próximo
  acionamento) e **Encerrar** (penalidades + confirmação do tempo). Lançamento
  manual continua disponível.
- **Inscrições** viram só *presença na etapa*: sem arma e sem quantidade. O RO
  escolhe a arma na hora; cada tempo lançado conta como uma passada.
- **Passadas (cobrança)**: lista de atiradores com passadas finalizadas por arma.
- **Estatísticas do atirador** no site público (`/estatisticas`): 1º disparo,
  split de cada tiro, quantidade, tempo total e penalidades, por passada.
- Cadastro de atiradores, lançamentos, classificação, etapas/mês (com PDFs e
  fechamento), categorias e usuários/cargos.

O **bot continua funcionando** em paralelo com o mesmo banco.

## Banco de dados — NADA é apagado

As migrações são aditivas (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN`):
tabelas novas `web_users`, `web_sessions`, `stage_checkins`, `run_shots` e
colunas novas em `runs` (`source`, `n_shots`, `first_shot`, `segments`,
`created_by_user`). O volume persistente `/app/data` já existente é mantido.
Faça backup do `tiro_guerra.db` antes do deploy, por precaução — como sempre.

## Deploy no Coolify

1. **Variáveis de ambiente** (adicionar às existentes):
   - `ADMIN_PASSWORD` = senha do primeiro login (usuário `admin`). Só é usada
     se ainda não existir nenhum usuário; depois pode até apagar a variável.
   - `SITE_URL` = `https://tirodeguerra.com.br` (QR do cupom). Opcional.
2. Redeploy. Confira o log: aparece `Área interna: usuário 'admin' criado`.
   Se não definir `ADMIN_PASSWORD`, a senha gerada aparece nesse log.
3. Entre em `/interno`, troque a senha em **Usuários** e crie os logins da
   recepção e dos ROs (cargos Recepção / RO, com as mesmas permissões do bot).

## No dia da etapa

- **Admin** abre a etapa em *Etapas*.
- **Recepção** marca presença em *Inscrições* (busca em tempo real; cadastra na
  hora se não existir). A aba *Passadas (cobrança)* mostra quantas cada um fez.
- **RO** abre *Linha de tiro* no Chrome (Android), conecta **Timer** e
  **Impressora** (botões no topo — um toque cada, o navegador pede pra escolher
  o aparelho), toca no atirador, escolhe a arma, confere e **Iniciar passada**.
  O timer bipa sozinho; os disparos aparecem na tela. **Encerrar** → ajusta
  penalidades → **Salvar** → o cupom sai automaticamente (ou pelo botão).
- Errou? *Lançamentos* apaga a passada e reimprime.
- Mantenha a tela do celular ligada com a página aberta (ela pede isso sozinha).

## Timer Special Pie M1A2 — protocolo (referência)

Serviço BLE `0xFFF0`: notificação em `0xFFF1`, comando em `0xFFF2`.
Quadro `F8 F9 <tipo> ... F9 F8`: `34` start, `36` disparo
(`segundos` 2 bytes BE + `centésimos` 1 byte + nº do disparo + nº da string),
`18` stop. Escrever `F8 F9 34 F9 F8` inicia; `F8 F9 18 F9 F8` para.
