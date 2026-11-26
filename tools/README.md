# Gerador do README de perfil

Este diretório gera o `README.md` do perfil e a arte em SVG que ele mostra. Nenhum SVG fica
versionado: uma GitHub Action refaz os 82 arquivos todo dia e os publica na branch `output`,
de onde o README os carrega. Se a Action falhar, a `output` continua com a arte do dia anterior.

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `content.json` | todo o texto, os contatos, os projetos e a stack (cada item com `name` e `icon`) |
| `build.py` | gera os SVG (`assets`) e o `README.md` (`readme`) |
| `snapshot.py` | busca no GitHub os dados do dia (contribuições e pushes) |
| `svgkit.py` | kit de SVG: mede fontes e desenha o texto em contornos |
| `icons/` | os 26 SVG originais do simple-icons (CC0), o `linkedin.svg` desenhado aqui, `LICENSE.md` e `icons.json` (cor da marca de cada ícone) |
| `requirements.in`, `requirements.txt` | dependências (`fonttools`, `pytest`), a segunda com hashes |
| `fonts/` | fontes usadas na arte, com as licenças em `fonts/licenses/` |
| `../tests/` | testes e dados fixos (`fixtures/snapshot.json`) |

## O que é gerado (82 arquivos, todos na raiz de `--out`)

- `hero-{desktop,mobile}-{dark,light}.svg` (4): a tela e-ink com nome, indicadores e o gráfico de 26 semanas.
- `proj-<slug>-{desktop,mobile}-{dark,light}.svg` (24): uma "página" por projeto, com ícone em pixels,
  título, data do último push, descrição e stack.
- `icon-<icone>-{light,dark}.svg` (54): os 27 ícones dos chips (4 contatos e 23 da stack) na cor da marca, ajustada para ter
  contraste de pelo menos 3,5:1 contra o fundo do `<kbd>` do GitHub (TypeScript e JavaScript mantêm a
  cor da marca com as letras sobre um quadrado).

Os números vêm do snapshot: projetos públicos = quantidade de `repos` (repositórios públicos que não são fork
e já receberam push); push mais recente = o
repositório com `pushed_at` mais novo, **sem contar o próprio repositório do perfil** (o publish diário
empurra para ele), desempate por nome. A data de cada projeto vem do `repo` dele.

## Gerar localmente

Precisa de [uv](https://docs.astral.sh/uv/). O `build.py` declara a única dependência
(`fonttools`, versão fixa) no próprio cabeçalho.

```bash
# arte a partir dos dados fixos de teste (reprodutível)
uv run tools/build.py assets --snapshot tests/fixtures/snapshot.json --out dist

# arte a partir dos dados de hoje (precisa de um token do GitHub no ambiente)
GITHUB_TOKEN=$(gh auth token) python3 tools/snapshot.py --out snapshot.json
uv run tools/build.py assets --snapshot snapshot.json --out dist

# README
uv run tools/build.py readme            # escreve README.md
uv run tools/build.py readme --check    # só confere; sai com 1 se estiver desatualizado
```

O `snapshot.py` usa só a biblioteca padrão. `dist/` e `snapshot.json` não são versionados.
A mesma saída para o mesmo snapshot e o mesmo conteúdo, byte a byte: a data vem de
`snapshot.today`, nunca do relógio.

## Testar

```bash
uv run --with-requirements tools/requirements.txt pytest -q
```

## Editar o conteúdo

1. Edite `tools/content.json` (textos, projetos, contatos, rodapé).
2. Rode `uv run tools/build.py readme` e versione o `README.md` junto.

O teste `test_readme_is_up_to_date` e o passo `readme --check` do workflow falham se o
`README.md` não corresponder ao JSON. Um projeto novo também precisa de um ícone de pixels
(`PIXEL_ICONS` em `build.py`); o gerador recusa uma lista de projetos que não bata com eles.
Todo projeto de `content.json` precisa estar entre os repositórios públicos do `login`; senão o
gerador para com erro e não escreve nada.

## O que o workflow faz

`.github/workflows/profile.yml` roda em pull request, em push na `master`, todo dia às 06:17 (Brasília) e sob demanda.

- **build** (só lê): testes, `readme --check`, `snapshot.py`, `build.py assets`. Fora de pull
  request, envia `dist/` como artefato de 1 dia.
- **publish** (escreve): só na `master`. Não executa nada do repositório: baixa o artefato e
  publica na branch `output` com git puro, num repositório novo de um commit só, com
  `push --force`.

Antes de publicar, o job confere só com shell que o artefato tem exatamente 82 arquivos
`.svg` comuns, na raiz, com nome `[a-z0-9][a-z0-9-]*.svg` (pasta `.git`, link simbólico,
subpasta ou outro tipo de arquivo derrubam o job).

Os limites do gerador: cada SVG com até 60.000 bytes e XML bem-formado. Interrompem a execução,
antes de publicar e sem escrever nada: snapshot inválido (chave ausente ou com tipo errado,
`weeks_26` que não tenha exatamente 26 semanas, `pushed_at` fora de AAAA-MM-DD, nenhum repositório
além do próprio perfil), chave ausente no `content.json`, ícone de stack ausente ou fora do formato
esperado, texto que não cabe (descrição do desktop, `desc_short` em 2 linhas no celular, linha de
stack, nome no celular abaixo de 28 px), título que encosta na data e rótulo que encosta no valor de
um indicador. Nada é truncado em silêncio. O `snapshot.py` também para
(sem imprimir o corpo nem o token) se a API do GitHub responder em formato inesperado.

Agendamento: o GitHub pode desativar workflows agendados de repositório público após 60 dias
sem atividade. Neste repositório, o push diário na `output` manteve o agendamento anterior
rodando por mais de 70 dias sem commits na branch padrão. Se a data de "atualizado" parar de
andar, reative o workflow em Actions.

## Atualizar dependências

`tools/requirements.in` é a fonte das versões (`fonttools`, `pytest`); `tools/requirements.txt`
é gerado dele, com hashes, e é o que o workflow instala (`--require-hashes`). Para atualizar:

1. edite a versão em `requirements.in`;
2. na raiz do repositório, recompile:
   `uv pip compile --generate-hashes --python-version 3.13 tools/requirements.in -o tools/requirements.txt`;
3. ajuste o `fonttools==` do cabeçalho de `build.py` (o `uv run` lê dali). O teste
   `test_fonttools_pin_is_the_same_everywhere` falha se os três não baterem;
4. rode os testes e confira que `build.py assets` com a fixture gera os mesmos 82 arquivos (ou que
   a diferença é esperada).

As Actions são fixadas por SHA; o Dependabot propõe as atualizações.

## Fontes e licenças

Literata (variável: 600 em pé e 400 itálico) e Departure Mono (`.otf`, CFF), ambas sob a SIL Open
Font License 1.1; os textos estão em `fonts/licenses/`. As instâncias de peso da Literata são
calculadas na primeira execução e guardadas em `fonts/.cache/` (ignorado pelo git); o nome do
arquivo no cache inclui um hash do TTF de origem, então trocar a fonte invalida o cache.

## Ícones da stack

Os SVG de `icons/` são os originais do [simple-icons](https://github.com/simple-icons/simple-icons)
(CC0 1.0, versão em `icons/icons.json`, campo `source`), sem alteração. A exceção é o `linkedin.svg`:
o simple-icons tirou o logo do LinkedIn, então o desenho é próprio, no mesmo formato (um `<path>` em
24×24), também CC0. Para atualizar:

1. baixe os SVG da versão fixada (de preferência com mais de duas semanas) para `tools/icons/`,
   trocando os arquivos e o `LICENSE.md`;
2. ajuste em `icons/icons.json` o campo `source` e, se a marca mudou de cor, o `hex` de cada ícone
   (`letters` é a cor das letras vazadas, só TypeScript e JavaScript);
3. para um ícone novo, acrescente o SVG e a entrada no `icons.json` e use o nome dele (`icon`) em
   `content.json`;
4. rode `uv run tools/build.py readme` e os testes. O gerador exige `viewBox="0 0 24 24"`, filhos
   exatamente `<title>` e `<path>` e um `d` só com comandos e números de caminho.
