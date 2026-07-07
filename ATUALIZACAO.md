# ATUALIZAÇÃO — Tiro de Guerra (Coolify)

## ⚠️ LEIA ANTES DE FAZER O DEPLOY

O print do Coolify mostra **Persistent Storage: "No storage found"** no recurso
*Tiro de Guerra*. Isso significa que o banco de dados (`/app/data/tiro_guerra.db`)
está gravado **dentro do container** — e **todo redeploy apaga o container e os
dados junto** (atiradores, resultados, equipe).

Ou seja: antes de subir esta versão nova, é obrigatório **(1) fazer backup do
banco atual** e **(2) criar o volume persistente**. Depois disso, os próximos
deploys nunca mais perdem dados.

As migrações do banco são automáticas e não destrutivas: no primeiro boot desta
versão, o código adiciona as colunas/tabelas novas (limites de categoria,
quantos sobem/descem, categoria inicial, modalidades, cargos e permissões)
preservando tudo o que já existe.

---

## Passo a passo

### 1. Backup do banco atual (via SSH no VPS da Hostinger)

```bash
ssh root@SEU_VPS

# descobre o nome do container do Tiro de Guerra
docker ps | grep -i tiro

# copia o banco para fora do container
docker cp NOME_DO_CONTAINER:/app/data/tiro_guerra.db /root/tiro_guerra_backup.db

# confere se copiou (deve mostrar o arquivo com tamanho > 0)
ls -lh /root/tiro_guerra_backup.db
```

*Alternativa sem SSH:* na aba **Terminal** do recurso no Coolify, rode
`base64 /app/data/tiro_guerra.db` e salve a saída num arquivo local
(depois restaure com `base64 -d`). O `docker cp` é bem mais simples.

### 2. Criar o volume persistente no Coolify

No recurso **Tiro de Guerra** → **Persistent Storage** → **+ Add**:

- Tipo: **Volume Mount**
- Name: `tiroguerra-data` (ou qualquer nome)
- Destination Path: `/app/data`

Salve. (Não precisa de Source Path para volume nomeado.)

### 3. Subir o código novo e fazer o Redeploy

Envie esta versão para o repositório Git ligado ao recurso (ou faça o upload
da forma que você já usa) e clique em **Redeploy**.

No primeiro boot, como o volume novo está vazio, o sistema vai carregar o
`seed/` antigo — não se preocupe, o passo 4 substitui pelo banco real.

### 4. Restaurar o banco real dentro do volume

```bash
# o container novo tem outro nome/id — descubra de novo
docker ps | grep -i tiro

# devolve o backup para dentro do volume
docker cp /root/tiro_guerra_backup.db NOME_DO_CONTAINER:/app/data/tiro_guerra.db
```

Depois clique em **Restart** no Coolify. No boot, as migrações rodam sozinhas
sobre o banco restaurado (aparece "Banco inicializado" no log).

### 5. Conferir

- Site abre e mostra a classificação de junho normalmente.
- No bot: `/start` → **⚙️ Administração** → **🏷️ Categorias** deve mostrar
  Pistola (3 categorias) e Carabina (1), com limites 8/8 e ⬆️3 ⬇️3 já
  configurados, e Combatente marcado como 🚪inicial.
- **👥 Equipe** → **🎖️ Cargos e permissões**: RO só com "Lançar e consultar
  resultados" (a inscrição saiu do menu dos ROs automaticamente).

A partir daqui, qualquer redeploy futuro preserva os dados, porque o banco
vive no volume e não mais no container.

---

## O que mudou nesta versão

1. **PDF no fechamento da etapa** — ao fechar a etapa, o bot envia um PDF no
   design do site com tempo, penalidades, tempo final e pontos de cada
   atirador, por categoria, só daquela etapa.
2. **Categorias (tipo → subcategoria)** — em Administração → Categorias:
   criar/renomear/excluir tipos (pistola, carabina, novos) e categorias;
   editar limite de atiradores, quantos sobem e quantos descem por mês; mover
   a categoria na hierarquia; definir a categoria inicial.
3. **Categoria inicial** — a inscrição não pergunta mais a categoria de
   atirador novo: ele entra na categoria inicial configurada. A categoria de
   qualquer atirador pode ser alterada manualmente em Atiradores → (atirador)
   → 🏷️ Alterar categoria.
4. **Lançamento de resultados** — primeiro escolhe o tipo (pistola/carabina/
   etc.); na lista, aparecem primeiro os que ainda não passaram (⏳, ordem
   alfabética) e depois os que já têm resultado (✅, ordem alfabética).
5. **Equipe, cargos e permissões** — "Inscrever atirador" saiu do menu dos
   ROs. O admin agora pode: renomear membro, alterar o cargo, remover;
   criar/renomear/excluir cargos e ligar/desligar as permissões de cada um
   (inscrever, resultados, atiradores). Os botões de aprovação de acesso
   usam os cargos cadastrados.
6. **Fechamento do mês** — a proposta de subidas/descidas agora segue a
   configuração de cada categoria (não mais 3/3 fixo) e avisa se alguma
   categoria ficar acima do limite de vagas.
7. **Site e PDFs dinâmicos** — categorias e tipos novos aparecem
   automaticamente no site, no PDF mensal e no PDF da etapa.
