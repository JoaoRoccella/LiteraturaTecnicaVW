# Leitor de CD técnico automotivo

Aplicação local em Python para ler diretamente da unidade óptica este CD e
outros que conservem a mesma organização. Não é necessário executar o programa
antigo, instalar o BDE, copiar os dados ou alterar os arquivos do CD.

## Requisitos e execução

- Python 3.11 ou superior.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), da Astral.
- Linux ou Windows, com unidade óptica e CD de dados compatível. A raiz do CD
  deve conter `dados`, incluindo `Carro.DB`, `Conjuntos.DB`, `Grupo.DB` e
  `VarGrupo.DB`. As imagens ficam normalmente em `imagens`.
- No Linux, o sistema precisa montar o CD e permitir sua leitura. O leitor
  tenta montá-lo automaticamente em modo somente leitura usando `udisksctl`
  caso ainda não esteja montado. Se o UDisks não estiver disponível ou
  negar permissão, monte o CD no gerenciador de arquivos e tente novamente.

No diretório desta aplicação:

```sh
uv sync
uv run leitor-cd --abrir
```

Abra `http://127.0.0.1:8000/` se o navegador não abrir automaticamente.
Encerre com **Ctrl+C**. Para mudar a porta:

```sh
uv run leitor-cd --porta 8080
```

Insira o CD antes ou depois de iniciar a aplicação. A tela solicita a mídia
quando não há CD compatível; ela verifica novamente a cada cinco segundos e
abre o conteúdo quando a mídia é reconhecida. Para trocar de CD, use o
procedimento normal de ejeção do sistema e insira o outro; a aplicação
descarta o catálogo anterior. Se houver várias mídias compatíveis, é mantida
a primeira selecionada enquanto estiver disponível. A detecção considera
unidades ópticas, não diretórios comuns nem imagens ISO montadas como loop.

O projeto e seu ambiente virtual ficam em um diretório gravável separado da
mídia. As dependências Python são instaladas pelo `uv`; o leitor Paradox é
escrito em Python e não requer biblioteca nativa. O servidor escuta apenas em
`127.0.0.1`, não possui autenticação e deve ser usado apenas em máquinas e
navegadores confiáveis.

## Como usar

- **Veículos e vistas:** escolha veículo, conjunto, grupo e variante para
  listar as vistas explodidas disponíveis. Selecione um círculo na imagem
  para ver o nome da peça; também é possível usar Tab e Enter ou Espaço.
  Quando a peça tem link do tipo 1, seu nome abre a busca por prefixo dos
  nomes de operações. Os resultados podem incluir várias variantes e devem
  ser escolhidos pelo caminho. A tabela original continua abaixo da imagem.
- **Buscar textos:** pesquisa sem diferenciar maiúsculas ou acentos no
  título, no caminho e no conteúdo dos arquivos `.txt`. O campo opcional
  restringe a pesquisa a uma pasta relativa à raiz do CD, por exemplo
  `dados/Gol`. A primeira pesquisa lê os textos diretamente do CD e cria um
  índice em memória, descartado quando a mídia é removida ou substituída.
- **Dados, Imagens e Arquivos:** percorra as pastas para abrir diretamente
  tabelas, textos, planilhas e figuras. Por exemplo, produtos químicos estão
  em `dados/quimicos/Produtos.DB`; tabelas de esquemas estão em
  `dados/esquemas`; vistas e suas peças estão em `imagens/ve` e `dados/ve`.
- Tabelas Paradox podem ser filtradas e paginadas. Os arquivos de texto
  exibem passos, notas, ferramentas, links para outros textos e figuras.
  Planilhas antigas `.xls` mostram o conteúdo das abas. Arquivos `.vf`
  mostram as listas de figuras e as imagens encontradas.

## Organização observada neste CD

O inventário da cópia analisada contém aproximadamente 25 mil textos,
12 mil GIFs, 2.786 arquivos `.db` e 1.303 planilhas `.xls`. A maior parte do
conteúdo técnico está em `dados` e `imagens`; o código Delphi está em
`Codigo_Fonte_Vol_11`. `DISK1`, `Disk2`, `Acrobat` e os executáveis pertencem
ao instalador e ao software antigo, não são necessários ao leitor. A
aplicação não executa esses arquivos.

Os `.DB` são tabelas **Paradox**, não SQLite. Há arquivos auxiliares `.PX`
de índice, `.MB` para campos memo e outros metadados; conserve-os junto das
tabelas ao copiar o CD. `dados/11.db` é um marcador vazio, não uma tabela.
Nesta cópia, as tabelas principais incluem:

| Arquivo/pasta | Função observada |
| --- | --- |
| `dados/Carro.DB`, `Conjuntos.DB`, `Grupo.DB`, `VarGrupo.DB` | Códigos e nomes de veículos, conjuntos, grupos e variantes. |
| `dados/carro_conjunto/CCNN.db` | Grupos de um veículo `CC` e conjunto `NN`. |
| `dados/carro_conjunto_grupo/CCNNGG.db` | Variantes de um grupo `GG` daquele veículo e conjunto. |
| `dados/carro_conjunto_grupo_vargr/GG/CCNNGGVV.db` | Códigos de vista `CodVE` de uma variante `VV`. Os códigos são preenchidos com dois dígitos. |
| `dados/ve/<vista>.db` | Peças, código de link, tipo e coordenadas da vista. |
| `imagens/ve/<vista>r.gif` | Imagem da vista explodida; alguns nomes usam outra convenção. |
| `dados/<veículo>/<sistema>/*.txt` | Operações, manutenção e informações técnicas, com pastas `auxi` para textos complementares. |
| `imagens/img` e `dados/Tabelas` | Figuras, planilhas Excel antigas e listas `.vf` de imagens. |
| `dados/esquemas`, `dados/quimicos`, `dados/Pintura`, `dados/Irregularidades` | Dados especializados de esquemas, produtos, pintura e irregularidades. |

Esses vínculos vêm dos códigos e das consultas do programa original; não se
deve presumir que o CD imponha integridade referencial. Os textos usam
principalmente Windows-1252 e marcadores como `title=`, `#`, `^`, `@`, `~`,
`&`, `$filename=...$` e `%filename=...%`. Referências `.bmp` normalmente
apontam para GIFs ou para uma lista `.vf`; `.xls` pode igualmente ter uma
lista `.vf` correspondente. A resolução ignora diferenças de maiúsculas e
minúsculas, como no Windows original. Referências ausentes ou ambíguas são
informadas na página.

O código original também associa os códigos 1–18 às pastas dos veículos.
O leitor só aplica esse mapeamento para restringir a busca se o nome do
veículo coincidir com o observado neste CD; caso contrário pesquisa todo o
CD e informa isso na vista.

## Limitações

- É um navegador dos dados, não uma reprodução integral da interface
  original: não implementa relatórios, relacionamentos completos de
  esquemas ou telas especializadas de química.
  As tabelas correspondentes continuam acessíveis pelo explorador.
- As áreas clicáveis das vistas usam as coordenadas da tabela e uma área
  circular ao redor de cada identificador. Coordenadas ausentes, inválidas ou
  fora da imagem são informadas e permanecem disponíveis na tabela.
- Os links de peça pesquisam pelo prefixo no nome dos textos e podem retornar
  operações de outras variantes. Confira veículo, sistema e variante antes
  de usar uma informação técnica.
- A leitura dos marcadores é uma interpretação prática, não emula todos os
  detalhes de apresentação do Delphi. Há arquivos originais com marcação
  incorreta ou referências ausentes. Textos com bytes não decodificáveis são
  exibidos com o caractere de substituição.
- Planilhas são exibidas como valores, sem preservar formatação, objetos,
  fórmulas ou imagens incorporadas; a visualização é limitada às primeiras
  200 linhas e 60 colunas por aba. Abra o arquivo original em software
  compatível se precisar do restante ou da aparência original.
- Bancos Paradox criptografados ou danificados podem não ser legíveis.
  Células inválidas e divergências de contagem são sinalizadas quando
  detectadas pela biblioteca. Índices auxiliares não são reconstruídos.
- O leitor não modifica os dados do CD, não importa nem exporta arquivos e
  não disponibiliza executáveis para execução. A compatibilidade com outros
  volumes depende de preservarem os nomes e estruturas relevantes.
- A montagem e as permissões dependem do sistema operacional. A detecção
  automática é implementada para Linux e Windows; outros sistemas não são
  suportados. A leitura e indexação podem ser lentas em unidades ópticas.

## Verificação

```sh
uv run python -m unittest discover -s tests -v
```

Os testes incluem simulação de inserção, remoção, troca, mídia incompatível e
montagem, além de exemplos temporários. Quando os dados de exemplo estão
presentes na pasta do projeto, também testam navegação, leitura Paradox,
texto com imagens e planilha. Na análise desta cópia, foram abertas todas as
2.783 tabelas não vazias de `dados`: 26.144 registros, sem células inválidas,
discrepâncias de contagem ou erros de leitura detectados. As 1.303 planilhas
também foram abertas sem erro e as 804 referências nas listas `.vf` foram
localizadas. Isso não valida a
correção técnica do conteúdo nem a integridade de todos os vínculos.
