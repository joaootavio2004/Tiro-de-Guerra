# Tiro de Guerra

Sistema de inscrições e resultados do **GUERRA Clube de Tiro**.

- **Site público** (`tirodeguerra.com.br`): classificação do mês por categoria,
  etapa ao vivo e estatísticas do atirador.
- **Área interna** (`admin.tirodeguerra.com.br`): recepção, linha de tiro (timer
  Bluetooth e impressora térmica), lançamentos, cadastro, etapas, categorias,
  indicadores e usuários.

## Pontuação

O menor tempo de cada categoria vale **100 pontos** e os demais recebem
`(menor tempo ÷ seu tempo) × 100`. No mês, somam-se as etapas **contando as
melhores**. O padrão é descartar ao menos a pior e contar no máximo 3
(3 etapas → contam 2; 4 → 3; 5 → 3); o administrador altera isso em
**Área interna › Etapas › Etapas que contam no mês**. Cada mês fechado guarda a
regra com que foi apurado. Penalidades são segundos somados ao tempo; DQ zera a
etapa.

## Implantação no Coolify

1. Recurso do tipo **Application** com build por **Dockerfile**, apontando para
   este repositório (ramo `main`), porta exposta **8000**. O
   `docker-compose.yml` serve só para rodar fora do Coolify.
2. **Domínios** (separados por vírgula):
   `https://tirodeguerra.com.br,https://admin.tirodeguerra.com.br`.
   Os dois precisam de registro DNS apontando para o servidor. Inclua o
   subdomínio **antes** de publicar uma versão com `ADMIN_HOST` ativo, pois o
   endereço antigo `/interno` passa a redirecionar para ele.
3. **Variáveis de ambiente**:
   - `ADMIN_PASSWORD`: senha do primeiro login (usuário `admin`). Só é usada se
     ainda não existir nenhum usuário. Sem ela, a senha gerada aparece no log.
   - `ADMIN_HOST`: domínio da área interna (padrão `admin.tirodeguerra.com.br`).
   - `SITE_URL`: endereço público, impresso no QR do cupom térmico.
   - Opcionais: `CLUB_NAME`, `TZ`, `DB_PATH`.
4. **Volume persistente** em `/app/data` (banco, backups das limpezas e,
   futuramente, fotos). Configure cópia periódica desse volume.
5. **Deploy**. No primeiro start com volume vazio, o histórico de `seed/` é
   carregado. Para começar do zero, apague `seed/` antes de implantar.

Endereços antigos (`tirodeguerra.com.br/interno/...`) redirecionam para o
subdomínio, mantendo o caminho.

## Banco de dados

SQLite num único arquivo. As migrações são aditivas (`CREATE TABLE IF NOT
EXISTS` / `ADD COLUMN`); nada é apagado automaticamente. As tabelas `staff` e
`access_requests` do antigo bot do Telegram podem existir em bancos antigos e
não são mais usadas. Faça backup do `tiro_guerra.db` antes de cada deploy.

## No dia da etapa

- **Admin** abre a etapa em *Etapas*.
- **Recepção** marca presença em *Inscrições* (busca em tempo real; cadastra na
  hora se não existir; CPF obrigatório). A aba *Passadas (cobrança)* mostra
  quantas passadas cada um fez.
- **RO** abre *Linha de tiro* no Chrome (Android), conecta **Timer** e
  **Impressora**, toca no atirador, escolhe a arma, confere a ficha e
  **Iniciar passada**. **Encerrar** → penalidades → confere a ficha →
  **Salvar**; o cupom sai automaticamente (ou pelo botão).
- Errou? *Lançamentos* apaga a passada e reimprime.

## Área interna: páginas

| Caminho | Quem | O que faz |
|---|---|---|
| `/linha` | RO | Lançar passada com timer/impressora, manual, somar/reiniciar |
| `/lancamentos` | RO | Conferir, apagar, reimprimir |
| `/inscricoes` | Recepção | Presença na etapa e passadas para cobrança |
| `/atiradores` | Recepção/Admin | Cadastro, CPF, categoria, mesclar, excluir |
| `/classificacao` | Todos | Etapa aberta e mês |
| `/indicadores` | Admin | Participação e passadas por etapa e por mês |
| `/etapas` | Admin | Abrir/fechar etapas, PDFs, regra do mês, fechar mês |
| `/categorias` | Admin | Modalidades, categorias e limpeza de cadastros sem uso |
| `/usuarios` | Admin | Logins, cargos e permissões |

As rotas são registradas sob `/interno` no código; `app/hosts.py` atende o
subdomínio sem esse prefixo.

## Timer Special Pie M1A2: protocolo (referência)

Serviço BLE `0xFFF0`: notificação em `0xFFF1`, comando em `0xFFF2`.
Quadro `F8 F9 <tipo> ... F9 F8`: `34` start, `36` disparo
(`segundos` 2 bytes BE + `centésimos` 1 byte + nº do disparo + nº da string),
`18` stop. Escrever `F8 F9 34 F9 F8` inicia; `F8 F9 18 F9 F8` para.

## Estrutura do projeto

```
app/
  website.py        site público
  interno.py        área interna (operação)
  interno_admin.py  área interna (administração)
  hosts.py          subdomínio da área interna
  db.py / webdb.py  banco de dados (SQLite)
  standings.py      classificações (etapa e mês)
  scoring.py        cálculo dos pontos
  report.py         PDFs de resultados
templates/  static/ páginas, estilos, ícones e scripts do navegador
scripts/import_planilha.py   importação da planilha antiga
seed/tiro_guerra.db          histórico inicial
run.py                       inicia o servidor web
```
