from __future__ import annotations

import argparse
import html
import inspect
import json
import logging
import mimetypes
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import xlrd

from .catalogo import Catalogo, ler_tabela, ler_texto, normalizar
from .midia import FonteCD, detector_sistema


LOG = logging.getLogger("leitor")
PAGE_SIZE = 200

LOADER = """<div id="leitura" class="leitura" role="status" aria-live="polite">
<div class="leitura-conteudo"><div class="leitura-spinner" aria-hidden="true"></div>
<p id="leitura-mensagem">Verificando e lendo dados do CD. Aguarde...</p>
<button id="leitura-tentar" hidden onclick="location.reload()">Tentar novamente</button></div></div>"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def url(route: str, **params: object) -> str:
    return route + ("?" + urlencode(params) if params else "")


def link(route: str, label: object, **params: object) -> str:
    return f'<a href="{esc(url(route, **params))}">{esc(label)}</a>'


def arquivo_link(catalogo: Catalogo, path: Path, label: str | None = None,
                 contexto: dict[str, str] | None = None) -> str:
    return link("/arquivo", label or path.name, path=catalogo.relativo(path), **(contexto or {}))


def pagina_carregando(direto: str) -> bytes:
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Carregando · CD técnico</title>
<link rel="stylesheet" href="/style.css"></head><body>{LOADER}
<noscript><style>.leitura{{display:none}}</style>
<p>JavaScript está desativado. <a href="{esc(direto)}">Abrir sem indicador de carregamento</a>.</p></noscript>
<script>
async function carregarPagina() {{
  try {{
    const resposta = await fetch(location.href, {{headers: {{'X-Leitor-Conteudo': '1'}}, cache: 'no-store'}});
    if (!(resposta.headers.get('content-type') || '').includes('text/html')) {{
      throw new Error('Resposta inesperada do servidor.');
    }}
    const pagina = await resposta.text();
    document.open();
    document.write(pagina);
    document.close();
  }} catch (erro) {{
    console.error('Não foi possível carregar a página:', erro);
    document.getElementById('leitura-mensagem').textContent =
      'Não foi possível carregar os dados. Verifique a unidade e tente novamente.';
    document.getElementById('leitura-tentar').hidden = false;
  }}
}}
carregarPagina();
</script></body></html>""".encode("utf-8")


def pagina(titulo: str, conteudo: str, identidade: str = "", trilha: str = "") -> bytes:
    identificador = json.dumps(identidade).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(titulo)} · CD técnico</title>
<link rel="stylesheet" href="/style.css"><noscript><style>.leitura{{display:none}}</style></noscript>
</head><body>{LOADER}<header><a href="/" class="header-brand"><img src="/logo" alt="Logo VolksWagen" class="header-logo"><strong>Literatura Técnica VolksWagen</strong></a>
<nav><a href="/veiculos">Veículos e vistas</a><a href="/buscar">Buscar textos</a>
<a href="/pasta?path=dados">Dados</a><a href="/pasta?path=imagens">Imagens</a>
<a href="/pasta">Arquivos</a></nav></header><main>{trilha}{conteudo}</main>
<script>
const midiaInicial = {identificador};
const leitura = document.getElementById('leitura');
window.addEventListener('load', () => {{ leitura.hidden = true; }});
window.addEventListener('pageshow', evento => {{
  if (evento.persisted) leitura.hidden = true;
}});
document.addEventListener('click', evento => {{
  const alvo = evento.target instanceof Element ? evento.target.closest('a') : null;
  if (!alvo || evento.defaultPrevented || evento.button !== 0 || evento.ctrlKey ||
      evento.metaKey || evento.shiftKey || evento.altKey || alvo.hasAttribute('download') ||
      (alvo.target && alvo.target !== '_self')) return;
  const destino = new URL(alvo.href, location.href);
  if (destino.origin !== location.origin ||
      (destino.pathname === location.pathname && destino.search === location.search && destino.hash)) return;
  leitura.hidden = false;
}});
document.addEventListener('submit', evento => {{
  if (!evento.defaultPrevented && evento.target instanceof HTMLFormElement &&
      (!evento.target.target || evento.target.target === '_self') &&
      new URL(evento.target.action, location.href).origin === location.origin) leitura.hidden = false;
}});
async function verificarMidia() {{
  try {{
    const resposta = await fetch('/status', {{cache: 'no-store'}});
    const estado = await resposta.json();
    if (estado.midia !== midiaInicial) {{
      leitura.hidden = false;
      window.location.replace('/');
    }}
    const aviso = document.getElementById('estado-cd');
    if (aviso) aviso.textContent = estado.mensagem;
  }} catch (erro) {{
    console.error('Não foi possível verificar o CD:', erro);
  }}
}}
setInterval(verificarMidia, 5000);
</script></body></html>""".encode("utf-8")


def paginacao(route: str, total: int, offset: int, **params: object) -> str:
    partes = [f'<span>{offset + 1 if total else 0}–{min(offset + PAGE_SIZE, total)} de {total}</span>']
    if offset > 0:
        partes.append(link(route, "← Anterior", offset=max(0, offset - PAGE_SIZE), **params))
    if offset + PAGE_SIZE < total:
        partes.append(link(route, "Próxima →", offset=offset + PAGE_SIZE, **params))
    return '<div class="pagination">' + "".join(partes) + "</div>"


def indice(c: Catalogo) -> str:
    return f"""<h1>Literatura técnica automotiva</h1>
<p>Explore os dados originais do CD em modo somente leitura.</p>
<p class="muted path">Origem: {esc(c.raiz)} · {len(c.arquivos):,} arquivos catalogados</p>
<div class="grid"><section class="card"><h2>Veículos e vistas</h2>
<p>Navegue pelos códigos originais de veículo, conjunto, grupo e variante.</p>{link('/veiculos', 'Abrir veículos →')}</section>
<section class="card"><h2>Busca nos textos</h2><p>Encontre operações pelo título, caminho ou conteúdo.</p>
{link('/buscar', 'Pesquisar →')}</section><section class="card"><h2>Arquivos do CD</h2>
<p>Explore textos, tabelas Paradox, planilhas e imagens, inclusive química e esquemas.</p>
{link('/pasta', 'Explorar arquivos →')}</section></div>"""


def pasta(c: Catalogo, path: str, offset: int) -> str:
    diretorio = c.caminho(path)
    if not diretorio.is_dir():
        raise FileNotFoundError("Pasta não encontrada.")
    entradas = sorted(
        (p for p in diretorio.iterdir() if not p.is_symlink() and p.name not in {".venv", "__pycache__", ".git", "leitor", "tests"}),
        key=lambda p: (not p.is_dir(), p.name.casefold()),
    )
    rel = c.relativo(diretorio)
    parent = c.relativo(diretorio.parent) if diretorio != c.raiz else None
    itens = []
    for entrada in entradas[offset:offset + PAGE_SIZE]:
        r = c.relativo(entrada)
        if entrada.is_dir():
            itens.append(f"<li>📁 {link('/pasta', entrada.name, path=r)}</li>")
        else:
            itens.append(f"<li>{arquivo_link(c, entrada)}</li>")
    voltar = f"<p>{link('/pasta', '← Pasta anterior', path=parent)}</p>" if parent is not None else ""
    return f"""<h1>Arquivos</h1><p class="path">{esc(rel or '/')}</p>{voltar}
<div class="card">{paginacao('/pasta', len(entradas), offset, path=rel)}
<ul class="files">{''.join(itens)}</ul>{paginacao('/pasta', len(entradas), offset, path=rel)}</div>"""


def busca(c: Catalogo, termo: str, offset: int, escopo: str = "", prefixo: bool = False,
          contexto: dict[str, str] | None = None) -> str:
    contexto = contexto or {}
    form = f"""<form action="/buscar"><input type="search" name="q" value="{esc(termo)}"
placeholder="Título, código, caminho ou texto" aria-label="Buscar textos">
<input name="escopo" value="{esc(escopo)}" placeholder="Pasta opcional" aria-label="Pasta opcional">
{campos_ocultos(contexto)}<button>Buscar</button></form>"""
    if not termo.strip():
        return f"<h1>Buscar textos</h1><div class='card'>{form}<p>A primeira busca indexa os textos do CD em memória.</p></div>"
    palavras = normalizar(termo).split()
    pasta = c.caminho(escopo) if escopo else c.raiz
    if not pasta.is_dir():
        raise ValueError("Pasta de busca não encontrada.")
    resultados = [d for d in c.indexar_documentos() if d.caminho.is_relative_to(pasta)
                  and (normalizar(d.caminho.stem).startswith(normalizar(termo)) if prefixo
                       else all(p in d.busca for p in palavras))]
    origem = contexto | {"busca_q": termo, "busca_escopo": escopo,
                         "busca_prefixo": str(int(prefixo)), "busca_offset": str(offset)}
    itens = "".join(f"<li>{arquivo_link(c, d.caminho, d.titulo, origem)}<br><small class='muted path'>{esc(c.relativo(d.caminho))}</small></li>" for d in resultados[offset:offset + PAGE_SIZE])
    aviso = f"<p class='warning'>Não foi possível indexar {len(c.erros_indexacao)} arquivo(s): {esc('; '.join(c.erros_indexacao[:5]))}</p>" if c.erros_indexacao else ""
    modo = "<p class='muted'>Operações com nome iniciado pelo código da peça. Uma nova busca pesquisa também o conteúdo.</p>" if prefixo else ""
    return f"<h1>Buscar textos</h1><div class='card'>{form}{modo}{aviso}{paginacao('/buscar', len(resultados), offset, q=termo, escopo=escopo, prefixo=int(prefixo), **contexto)}<ol>{itens}</ol></div>"


def referencias_html(c: Catalogo, origem: Path, nome: str, imagem: bool,
                     contexto: dict[str, str] | None = None) -> str:
    if Path(nome).suffix.casefold() in {".bmp", ".xls"}:
        vf = c.localizar(f"dados/Tabelas/{Path(nome).stem}.vf")
        if vf:
            figuras = [linha.strip() for linha in ler_texto(vf).splitlines() if linha.strip()]
            if not figuras:
                return f"<p class='warning'>Lista de figuras vazia: {esc(c.relativo(vf))}</p>"
            return "".join(referencias_html(c, vf, figura + ".gif", True, contexto) for figura in figuras)
    candidatos = c.referencias(origem, nome)
    if not candidatos:
        return f'<p class="warning">Referência não encontrada: {esc(nome)}</p>'
    if len(candidatos) > 1:
        return f"<p>Referência ambígua {esc(nome)}:</p><ul>" + "".join(
            f"<li>{arquivo_link(c, p, c.relativo(p), contexto)}</li>" for p in candidatos
        ) + "</ul>"
    destino = candidatos[0]
    if imagem and destino.suffix.casefold() in {".gif", ".jpg", ".jpeg", ".png", ".bmp"}:
        src = url("/imagem", path=c.relativo(destino))
        return f'<figure class="figure"><img loading="lazy" src="{esc(src)}" alt="{esc(nome)}"><figcaption>{arquivo_link(c, destino, contexto=contexto)}</figcaption></figure>'
    return f"<p>↗ {arquivo_link(c, destino, nome, contexto)}</p>"


def documento(c: Catalogo, path: Path, contexto: dict[str, str] | None = None) -> str:
    linhas = ler_texto(path).splitlines()
    titulo = path.name
    saida = []
    ferramentas = False
    for linha in linhas:
        texto = linha.strip()
        if texto.lower().startswith("title="):
            titulo = texto[6:].strip()
            continue
        if ferramentas:
            fechou = texto.endswith("&")
            saida.append(f"<p><strong>{esc(texto.removesuffix('&'))}</strong></p>")
            ferramentas = not fechou
            continue
        match = re.fullmatch(r"([$%])filename=(.*?)\1", texto, flags=re.IGNORECASE)
        if match:
            saida.append(referencias_html(c, path, match.group(2), match.group(1) == "%", contexto))
        elif texto.startswith("#"):
            saida.append(f"<p>• {esc(texto[1:])}</p>")
        elif texto.startswith(("^", "@", "~")):
            saida.append(f'<p class="nota">{esc(texto[1:])}</p>')
        elif texto.startswith("&"):
            fechou = texto.endswith("&") and len(texto) > 1
            saida.append(f"<p><strong>Ferramenta: {esc(texto[1:].removesuffix('&'))}</strong></p>")
            ferramentas = not fechou
        elif texto:
            saida.append(f"<p>{esc(texto)}</p>")
    return f"<h1>{esc(titulo)}</h1><p class='muted path'>{esc(c.relativo(path))}</p><div class='card doc'>{''.join(saida)}</div>"


def tabela(c: Catalogo, path: Path, termo: str, offset: int, escopo: str = "",
           contexto: dict[str, str] | None = None) -> str:
    contexto = contexto or {}
    campos, linhas, aviso = ler_tabela(path)
    if termo:
        chave = normalizar(termo)
        linhas = [linha for linha in linhas if any(chave in normalizar(valor) for valor in linha.values())]
    cabecalho = "".join(f"<th>{esc(campo)}</th>" for campo in campos)
    linhas_html = []
    for linha in linhas[offset:offset + PAGE_SIZE]:
        celulas = []
        for campo in campos:
            valor = linha[campo]
            if campo.casefold() == "codve" and valor:
                anterior = contexto_vista(contexto)
                anterior.pop("vista", None)
                conteudo = link("/vista", valor, codigo=valor, **anterior)
            elif (campo.casefold() == "link" and valor and path.parent.name.casefold() == "ve"
                  and linha.get("TipoDoLink") == "1"):
                conteudo = link("/buscar", valor, q=valor, escopo=escopo, prefixo=1, **contexto_vista(contexto))
            else:
                conteudo = esc(valor)
            celulas.append(f"<td>{conteudo}</td>")
        linhas_html.append("<tr>" + "".join(celulas) + "</tr>")
    rel = c.relativo(path)
    aviso_html = f"<p class='warning'>{esc(aviso)}</p>" if aviso else ""
    return f"""<h1>Tabela: {esc(path.name)}</h1><p class="muted path">{esc(rel)}</p>{aviso_html}
<div class="card"><form action="/arquivo"><input type="hidden" name="path" value="{esc(rel)}">
<input type="search" name="q" value="{esc(termo)}" placeholder="Filtrar registros" aria-label="Filtrar registros">{campos_ocultos(contexto)}<button>Filtrar</button></form>
{paginacao('/arquivo', len(linhas), offset, path=rel, q=termo, **contexto)}
<table><thead><tr>{cabecalho}</tr></thead><tbody>{''.join(linhas_html)}</tbody></table></div>"""


def planilha(c: Catalogo, path: Path, sheet: int, contexto: dict[str, str] | None = None) -> str:
    livro = xlrd.open_workbook(path, on_demand=True)
    try:
        if sheet < 0 or sheet >= livro.nsheets:
            raise ValueError("Aba da planilha não encontrada.")
        aba = livro.sheet_by_index(sheet)
        opcoes = " · ".join(link("/arquivo", nome, path=c.relativo(path), sheet=i, **(contexto or {})) for i, nome in enumerate(livro.sheet_names()))
        corpo = []
        for linha in range(min(aba.nrows, 200)):
            celulas = "".join(f"<td>{esc(aba.cell_value(linha, coluna))}</td>" for coluna in range(min(aba.ncols, 60)))
            corpo.append(f"<tr>{celulas}</tr>")
        aviso = "<p class='warning'>Exibição limitada às primeiras 200 linhas e 60 colunas. Consulte o arquivo original para ver a planilha completa e sua formatação.</p>" if aba.nrows > 200 or aba.ncols > 60 else ""
        return f"<h1>Planilha: {esc(path.name)}</h1><p>{opcoes}</p>{aviso}<div class='card'><table>{''.join(corpo)}</table></div>"
    finally:
        livro.release_resources()


def obter(c: Catalogo, relativo: str) -> Path:
    path = c.localizar(relativo)
    if path is None:
        raise FileNotFoundError(f"Arquivo não encontrado: {relativo}")
    return path


def ler_lista(c: Catalogo, relativo: str) -> list[dict[str, str]]:
    _, linhas, _ = ler_tabela(obter(c, relativo))
    return linhas


NIVEIS = (
    ("carro", "Veículo", "dados/Carro.DB", "CodCarro", "NCarro"),
    ("conjunto", "Conjunto", "dados/Conjuntos.DB", "CodConjunto", "NConjunto"),
    ("grupo", "Grupo", "dados/Grupo.DB", "CodGrupo", "NGrupo"),
    ("variante", "Variante", "dados/VarGrupo.DB", "CodVarGrupo", "NVarGrupo"),
)


def contexto_vista(params: dict[str, str]) -> dict[str, str]:
    contexto = {}
    for chave, _, _, _, _ in NIVEIS:
        valor = params.get(chave, "")
        if not valor or not valor.isascii() or not valor.isdecimal() or int(valor) > 999:
            break
        contexto[chave] = valor
    codigo = params.get("vista", "")
    if codigo and re.fullmatch(r"[a-zA-Z0-9_-]+", codigo):
        contexto["vista"] = codigo
    return contexto


def contexto_busca(params: dict[str, str]) -> dict[str, str]:
    contexto = contexto_vista(params)
    if params.get("busca_q"):
        for chave in ("busca_q", "busca_escopo", "busca_prefixo", "busca_offset"):
            if chave in params:
                contexto[chave] = params[chave]
    return contexto


def campos_ocultos(params: dict[str, str]) -> str:
    return "".join(f'<input type="hidden" name="{esc(chave)}" value="{esc(valor)}">'
                   for chave, valor in params.items())


def renderizar_trilha(itens: list[tuple[str, str]]) -> str:
    partes = []
    for i, (nome, endereco) in enumerate(itens):
        if i == len(itens) - 1:
            partes.append(f'<li aria-current="page">{esc(nome)}</li>')
        else:
            partes.append(f'<li><a href="{esc(endereco)}">{esc(nome)}</a></li>')
    return '<nav class="breadcrumb" aria-label="Caminho de navegação"><ol>' + "".join(partes) + "</ol></nav>"


def trilha_navegacao(c: Catalogo, rota: str, params: dict[str, str]) -> str:
    itens: list[tuple[str, str]] = [("Início", "/")]

    def niveis(contexto: dict[str, str]) -> None:
        itens.append(("Veículos e vistas", "/veiculos"))
        anteriores: dict[str, str] = {}
        for chave, descricao, tabela, codigo, nome in NIVEIS:
            valor = contexto.get(chave)
            if not valor:
                break
            anteriores[chave] = valor
            linhas = ler_lista(c, tabela)
            legenda = next((r[nome] for r in linhas if r[codigo] == valor and r[nome]), valor)
            itens.append((f"{descricao}: {legenda}", url("/veiculos", **anteriores)))

    def vista_anterior(contexto: dict[str, str]) -> None:
        codigo = contexto.get("vista", "")
        if codigo:
            niveis(contexto)
            anteriores = {k: v for k, v in contexto.items() if k != "vista"}
            itens.append((f"Vista {codigo}", url("/vista", codigo=codigo, **anteriores)))

    def busca_anterior(contexto: dict[str, str], q: str, escopo: str, prefixo: str, offset: str) -> None:
        vista_anterior(contexto)
        parametros = dict(contexto)
        itens.append(("Buscar textos", url("/buscar", **parametros)))
        if q:
            parametros.update(q=q, escopo=escopo, prefixo=prefixo, offset=offset)
            itens.append((f"Busca: {q}", url("/buscar", **parametros)))

    def pastas(relativo: Path, incluir_ultima: bool) -> None:
        itens.append(("Arquivos", "/pasta"))
        partes = relativo.parts if incluir_ultima else relativo.parts[:-1]
        caminho = Path()
        for parte in partes:
            caminho /= parte
            itens.append((parte, url("/pasta", path=caminho.as_posix())))

    contexto = contexto_vista(params)
    if rota == "/":
        pass
    elif rota == "/veiculos":
        niveis(contexto)
    elif rota == "/vista":
        contexto["vista"] = params.get("codigo", "")
        vista_anterior(contexto)
    elif rota == "/buscar":
        busca_anterior(contexto, params.get("q", ""), params.get("escopo", ""),
                       params.get("prefixo", ""), params.get("offset", "0"))
    elif rota == "/pasta":
        relativo = Path(c.relativo(c.caminho(params.get("path", ""))))
        pastas(relativo, True)
    elif rota == "/arquivo":
        relativo = Path(c.relativo(obter(c, params.get("path", ""))))
        if params.get("busca_q"):
            busca_anterior(contexto, params["busca_q"], params.get("busca_escopo", ""),
                           params.get("busca_prefixo", ""), params.get("busca_offset", "0"))
        elif contexto.get("vista"):
            vista_anterior(contexto)
        else:
            pastas(relativo, False)
        itens.append((relativo.name, ""))
    else:
        return ""
    return renderizar_trilha(itens)


def veiculos(c: Catalogo, params: dict[str, str]) -> str:
    titulos = [(chave, descricao) for chave, descricao, _, _, _ in NIVEIS]
    selecionados = {k: params[k] for k, _ in titulos if params.get(k)}
    for valor in selecionados.values():
        if not valor.isdecimal() or int(valor) > 999:
            raise ValueError("Código de navegação inválido.")
    corpo = "<h1>Veículos e vistas</h1>"
    carro, conjunto, grupo, variante = (selecionados.get(k) for k, _ in titulos)
    if carro is None:
        opcoes = [(r["CodCarro"], r["NCarro"]) for r in ler_lista(c, "dados/Carro.DB") if r["CodCarro"]]
        nivel = "carro"
    elif conjunto is None:
        conjuntos = ler_lista(c, "dados/Conjuntos.DB")
        opcoes = [(r["CodConjunto"], r["NConjunto"]) for r in conjuntos
                  if r["CodConjunto"].isdigit() and c.localizar(f"dados/carro_conjunto/{int(carro):02d}{int(r['CodConjunto']):02d}.db")]
        nivel = "conjunto"
    elif grupo is None:
        codigos = {r["CodGrupo"] for r in ler_lista(c, f"dados/carro_conjunto/{int(carro):02d}{int(conjunto):02d}.db")}
        opcoes = [(r["CodGrupo"], r["NGrupo"]) for r in ler_lista(c, "dados/Grupo.DB") if r["CodGrupo"] in codigos]
        nivel = "grupo"
    elif variante is None:
        codigos = {r["CodVarGrupo"] for r in ler_lista(c, f"dados/carro_conjunto_grupo/{int(carro):02d}{int(conjunto):02d}{int(grupo):02d}.db")}
        opcoes = [(r["CodVarGrupo"], r["NVarGrupo"]) for r in ler_lista(c, "dados/VarGrupo.DB") if r["CodVarGrupo"] in codigos]
        nivel = "variante"
    else:
        rel = f"dados/carro_conjunto_grupo_vargr/{int(grupo):02d}/{int(carro):02d}{int(conjunto):02d}{int(grupo):02d}{int(variante):02d}.db"
        opcoes = [(r["CodVE"], r["CodVE"]) for r in ler_lista(c, rel)]
        nivel = "vista"
    corpo += "<div class='card'>"
    corpo += f"<h2>{esc(dict(titulos).get(nivel, 'Vistas explodidas'))}</h2><ul>"
    for codigo, nome in opcoes:
        if nivel == "vista":
            item = link("/vista", nome, codigo=codigo, **selecionados)
        else:
            item = link("/veiculos", f"{nome} ({codigo})", **(selecionados | {nivel: codigo}))
        corpo += f"<li>{item}</li>"
    return corpo + "</ul></div>"


def imagem_interativa(c: Catalogo, imagem: Path | None, linhas: list[dict[str, str]], escopo: str,
                      contexto: dict[str, str] | None = None) -> str:
    largura = altura = 0
    if imagem:
        with imagem.open("rb") as arquivo_imagem:
            cabecalho = arquivo_imagem.read(10)
        if len(cabecalho) != 10 or cabecalho[:6] not in (b"GIF87a", b"GIF89a"):
            raise ValueError(f"Imagem GIF inválida: {c.relativo(imagem)}")
        largura = int.from_bytes(cabecalho[6:8], "little")
        altura = int.from_bytes(cabecalho[8:10], "little")
        if not largura or not altura:
            raise ValueError(f"Dimensões inválidas na imagem: {c.relativo(imagem)}")

    # Primeira passagem: coleta e validação de dados
    dados_pecas = []
    problemas = []
    for indice_original, linha in enumerate(linhas, 1):
        nome = linha.get("NomePeca", "")
        if not nome:
            problemas.append(f"linha {indice_original}: nome da peça ausente")
            continue
        destino = ""
        if linha.get("TipoDoLink") == "1":
            codigo = linha.get("Link", "")
            if codigo:
                destino = url("/buscar", q=codigo, escopo=escopo, prefixo=1, **(contexto or {}))
            else:
                problemas.append(f"linha {indice_original}: código de link ausente")
        
        x = y = None
        localizado = False
        centro_x = centro_y = None
        if imagem:
            try:
                x, y = int(linha["X"]), int(linha["Y"])
            except (KeyError, ValueError):
                problemas.append(f"linha {indice_original}: coordenadas inválidas")
            else:
                # As coordenadas do CD são a origem da área de 45 px do programa original.
                centro_x, centro_y = x + 15, y + 15
                if 0 <= centro_x < largura and 0 <= centro_y < altura:
                    localizado = True
                else:
                    problemas.append(f"linha {indice_original}: ponto fora da imagem")
        
        dados_pecas.append({
            "indice_original": indice_original,
            "nome": nome,
            "destino": destino,
            "localizado": localizado,
            "x": x,
            "y": y,
            "centro_x": centro_x,
            "centro_y": centro_y,
        })
    
    # Segunda passagem: ordena por Y (cima-baixo), depois X (esquerda-direita)
    # Peças com coordenadas inválidas vão para o final
    pecas_localizadas = [p for p in dados_pecas if p["localizado"]]
    pecas_localizadas.sort(key=lambda p: (p["centro_y"], p["centro_x"]))
    pecas_nao_localizadas = [p for p in dados_pecas if not p["localizado"]]
    pecas_ordenadas = pecas_localizadas + pecas_nao_localizadas
    
    # Terceira passagem: gera pontos SVG e lista de peças com IDs
    pontos = []
    pecas = []
    for id_peca, dados in enumerate(pecas_ordenadas, 1):
        ponto_id = f"vista-ponto-{id_peca}"
        classe = "vista-peca-com-link" if dados["destino"] else "vista-peca-sem-link"
        
        if dados["localizado"]:
            # Adiciona círculo SVG com ID centralizado dentro
            pontos.append(
                f'<circle id="{ponto_id}" class="vista-ponto" cx="{dados["centro_x"]}" cy="{dados["centro_y"]}" r="20" '
                f'role="button" tabindex="0" aria-pressed="false" aria-label="Peça {id_peca}: {esc(dados["nome"])}" '
                f'data-nome="{esc(dados["nome"])}" data-destino="{esc(dados["destino"])}" data-id="{id_peca}"><title>{id_peca} - {esc(dados["nome"])}</title></circle>'
                f'<text class="vista-ponto-id-sombra" x="{dados["centro_x"]}" y="{dados["centro_y"]}" dy="0.3em">{id_peca}</text>'
                f'<text class="vista-ponto-id" x="{dados["centro_x"]}" y="{dados["centro_y"]}" dy="0.3em">{id_peca}</text>'
            )
            nome_html = f'<a class="vista-peca-nome {classe}" href="#{ponto_id}" data-id="{id_peca}">{esc(dados["nome"])}</a>'
        else:
            nome_html = (f'<span class="vista-peca-nome {classe}" data-id="{id_peca}">{esc(dados["nome"])}'
                         '<span class="vista-peca-indisponivel">Localização indisponível</span></span>')
        
        abrir = (f'<a class="vista-abrir" href="{esc(dados["destino"])}" '
                 f'aria-label="Abrir busca de textos para {esc(dados["nome"])}">Buscar ↗</a>' if dados["destino"] else "")
        
        id_span = f'<span class="vista-peca-id">{id_peca}</span>'
        pecas.append(f"<li>{id_span}{nome_html}{abrir}</li>")
    
    aviso = ""
    if problemas:
        aviso = f'<p class="warning">Não foi possível mapear alguns dados: {esc("; ".join(problemas))}.</p>'
    lista = f'<section class="card"><h2>Peças da vista</h2><ul class="vista-pecas">{"".join(pecas)}</ul></section>'
    if not imagem:
        return aviso + lista
    src = url("/imagem", path=c.relativo(imagem))
    return f"""<div class="card">
<p>Selecione um círculo da imagem para identificar a peça. Use Tab e Enter ou Espaço pelo teclado.</p>
<p id="peca-selecionada" aria-live="polite">Nenhuma peça selecionada.</p>{aviso}
<svg class="vista-svg" style="max-width:{largura}px" viewBox="0 0 {largura} {altura}"
     role="group" aria-label="Vista explodida com peças selecionáveis">
<image href="{esc(src)}" width="{largura}" height="{altura}" />
{''.join(pontos)}</svg></div>{lista}
<script>
const pontosVista = document.querySelectorAll('.vista-ponto');
const imagemVista = document.querySelector('.vista-svg');
const nomesVista = document.querySelectorAll('a.vista-peca-nome');
function selecionarPeca(ponto) {{
  pontosVista.forEach(item => item.setAttribute('aria-pressed', String(item === ponto)));
  nomesVista.forEach(item => {{
    if (item.getAttribute('href') === '#' + ponto.id) item.setAttribute('aria-current', 'true');
    else item.removeAttribute('aria-current');
  }});
  const destino = ponto.dataset.destino;
  const painel = document.getElementById('peca-selecionada');
  painel.replaceChildren();
  painel.classList.add('vista-peca-ativa');
  if (destino) {{
    const nome = document.createElement('a');
    nome.href = destino;
    nome.textContent = ponto.dataset.nome;
    nome.setAttribute('aria-label', ponto.dataset.nome + ' — abre a busca de textos');
    nome.title = 'Abre a busca de textos';
    const icone = document.createElement('span');
    icone.className = 'vista-peca-icone';
    icone.setAttribute('aria-hidden', 'true');
    icone.textContent = '↗';
    nome.append(icone);
    painel.append(nome);
  }} else {{
    painel.textContent = ponto.dataset.nome;
  }}
}}
nomesVista.forEach(nome => {{
  nome.addEventListener('click', evento => {{
    evento.preventDefault();
    const ponto = document.getElementById(nome.getAttribute('href').slice(1));
    selecionarPeca(ponto);
    ponto.focus({{preventScroll: true}});
    ponto.scrollIntoView({{block: 'center', inline: 'nearest'}});
  }});
}});
imagemVista.addEventListener('click', evento => {{
  const matriz = imagemVista.getScreenCTM();
  if (!matriz) return;
  const posicao = imagemVista.createSVGPoint();
  posicao.x = evento.clientX;
  posicao.y = evento.clientY;
  const local = posicao.matrixTransform(matriz.inverse());
  let escolhido = null;
  let menorDistancia = Infinity;
  pontosVista.forEach(ponto => {{
    const dx = local.x - ponto.cx.baseVal.value;
    const dy = local.y - ponto.cy.baseVal.value;
    const distancia = dx * dx + dy * dy;
    if (distancia <= ponto.r.baseVal.value ** 2 && distancia < menorDistancia) {{
      escolhido = ponto;
      menorDistancia = distancia;
    }}
  }});
  if (escolhido) selecionarPeca(escolhido);
}});
pontosVista.forEach(ponto => {{
  ponto.addEventListener('keydown', evento => {{
    if (evento.key === 'Enter' || evento.key === ' ') {{
      evento.preventDefault();
      selecionarPeca(ponto);
    }}
  }});
}});
</script>"""


def vista(c: Catalogo, codigo: str, carro: str = "", contexto: dict[str, str] | None = None) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", codigo):
        raise ValueError("Código de vista inválido.")
    corpo = f"<h1>Vista explodida: {esc(codigo)}</h1>"
    imagem = c.localizar(f"imagens/ve/{codigo}r.gif") or c.localizar(f"imagens/ve/{codigo}.gif")
    modelos = (
        ("Gol 95 >", "Gol"), ("Parati 96 >", "Parati"), ("Saveiro 98 >", "Saveiro"),
        ("Santana 91 >", "Santana"), ("Quantum 92 >", "Quantum"), ("Kombi", "KOMBI"),
        ("Fusca", "Fusca"), ("Gol GIII", "GolIII"), ("Parati GIII", "ParatiIII"),
        ("Gol 80 >", "GolBX"), ("Parati 82 >", "ParatiBX"), ("Saveiro 82 >", "SaveiroBX"),
        ("Voyage 81 >", "VoyageBX"), ("Logus", "Logus"), ("Pointer", "Pointer"),
        ("Santana 84 >", "SantanaV"), ("Quantum 85 >", "QuantumV"), ("Apollo", "Apollo"),
    )
    escopo = ""
    if carro:
        if not carro.isdecimal():
            raise ValueError("Código de veículo inválido.")
        carros = ler_lista(c, "dados/Carro.DB")
        nomes = [r["NCarro"] for r in carros if r["CodCarro"] == carro]
        numero = int(carro)
        if 1 <= numero <= len(modelos) and nomes == [modelos[numero - 1][0]]:
            nome_pasta = modelos[numero - 1][1]
            pasta = next((p for p in c.dados.iterdir()
                          if p.is_dir() and not p.is_symlink() and p.name.casefold() == nome_pasta.casefold()), None)
            if pasta:
                escopo = c.relativo(pasta)
                corpo += f"<p class='muted'>Busca de operações limitada a {esc(escopo)}.</p>"
        if not escopo:
            corpo += "<p class='warning'>Pasta do veículo não identificada; a busca de operações abrangerá todo o CD.</p>"
    db = c.localizar(f"dados/ve/{codigo}.db")
    origem = contexto_vista({"carro": carro, **(contexto or {}), "vista": codigo})
    if not imagem:
        corpo += "<p class='warning'>Imagem da vista não encontrada.</p>"
    if db:
        _, linhas, aviso = ler_tabela(db)
        if aviso:
            corpo += f'<p class="warning">{esc(aviso)}</p>'
        corpo += imagem_interativa(c, imagem, linhas, escopo, origem)
    elif imagem:
        corpo += f'<div class="card"><img src="{esc(url("/imagem", path=c.relativo(imagem)))}" alt="Vista {esc(codigo)}"></div>'
    if not db:
        corpo += "<p class='warning'>Tabela de peças da vista não encontrada.</p>"
    return corpo


def arquivo(c: Catalogo, rel: str, termo: str, offset: int, sheet: int,
            contexto: dict[str, str] | None = None) -> str:
    path = obter(c, rel)
    ext = path.suffix.casefold()
    if ext == ".txt":
        return documento(c, path, contexto)
    if ext == ".db":
        return tabela(c, path, termo, offset, contexto=contexto)
    if ext == ".xls":
        return planilha(c, path, sheet, contexto)
    if ext in {".gif", ".jpg", ".jpeg", ".png", ".bmp"}:
        return f'<h1>{esc(path.name)}</h1><div class="card"><img src="{esc(url("/imagem", path=c.relativo(path)))}" alt="{esc(path.name)}"></div>'
    if ext == ".vf":
        texto = ler_texto(path)
        figuras = "".join(referencias_html(c, path, linha.strip() + ".gif", True, contexto)
                          for linha in texto.splitlines() if linha.strip())
        return f"<h1>{esc(path.name)}</h1><p>Referências originais:</p><div class='card'><pre>{esc(texto)}</pre>{figuras}</div>"
    return f"<h1>{esc(path.name)}</h1><p class='path'>{esc(rel)}</p><p>Formato auxiliar ou executável legado; este leitor não o executa nem interpreta.</p>"


class Servidor(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], fonte: FonteCD):
        super().__init__(address, Requisicao)
        self.fonte = fonte
        self.trava = threading.Lock()


class Requisicao(BaseHTTPRequestHandler):
    server: Servidor

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if (parsed.path not in {"/imagem", "/status"} and
                "text/html" in self.headers.get("Accept", "") and
                self.headers.get("X-Leitor-Conteudo") != "1" and
                ("__direto", "1") not in params):
            direto = parsed.path + "?" + urlencode(params + [("__direto", "1")])
            self.responder(200, pagina_carregando(direto), "text/html; charset=utf-8")
            return
        with self.server.trava:
            self.responder_conteudo()

    def responder_conteudo(self) -> None:
        parsed = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        fonte = self.server.fonte
        identidade = ""
        trilha = ""
        if parsed.path == "/style.css":
            main_file = Path(inspect.getfile(Requisicao))
            css_path = main_file.parent / "static" / "style.css"
            if css_path.exists():
                self.responder(200, css_path.read_bytes(), "text/css; charset=utf-8")
            else:
                self.responder(404, b"CSS nao encontrado", "text/plain; charset=utf-8")
            return
        if parsed.path == "/logo":
            main_file = Path(inspect.getfile(Requisicao))
            logo_path = main_file.parent / "static" / "logo_vw.webp"
            if logo_path.exists():
                self.responder(200, logo_path.read_bytes(), "image/webp")
            else:
                self.responder(404, b"Logo nao encontrado", "text/plain; charset=utf-8")
            return
        try:
            c = fonte.atualizar()
            identidade = fonte.midia.identidade if fonte.midia else ""
            if parsed.path == "/status":
                self.responder(200, json.dumps({"midia": identidade, "mensagem": fonte.mensagem}).encode("utf-8"), "application/json; charset=utf-8")
                return
            if c is None:
                if parsed.path == "/imagem":
                    self.responder(503, b"CD indisponivel", "text/plain; charset=utf-8")
                    return
                conteudo = f"""<h1>Insira o CD</h1>
<div class="card"><p id="estado-cd" class="warning">{esc(fonte.mensagem)}</p>
<p>Insira um CD compatível e aguarde a montagem pelo sistema. A detecção será refeita automaticamente.</p>
<p>{link('/', 'Verificar novamente')}</p></div>"""
                trilha = renderizar_trilha([("Início", "/"), ("Aguardando CD", "")])
                self.responder(200, pagina("Aguardando CD", conteudo, trilha=trilha), "text/html; charset=utf-8")
                return
            offset = max(0, int(query.get("offset", "0")))
            if parsed.path == "/imagem":
                path = obter(c, query.get("path", ""))
                if path.suffix.casefold() not in {".gif", ".jpg", ".jpeg", ".png", ".bmp"}:
                    raise ValueError("Formato de imagem não permitido.")
                data = path.read_bytes()
                self.responder(200, data, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                return
            if parsed.path == "/":
                conteudo = indice(c)
            elif parsed.path == "/pasta":
                conteudo = pasta(c, query.get("path", ""), offset)
            elif parsed.path == "/buscar":
                conteudo = busca(c, query.get("q", ""), offset, query.get("escopo", ""),
                                 query.get("prefixo") == "1", contexto_vista(query))
            elif parsed.path == "/arquivo":
                conteudo = arquivo(c, query.get("path", ""), query.get("q", ""), offset,
                                   int(query.get("sheet", "0")), contexto_busca(query))
            elif parsed.path == "/veiculos":
                conteudo = veiculos(c, query)
            elif parsed.path == "/vista":
                conteudo = vista(c, query.get("codigo", ""), query.get("carro", ""), contexto_vista(query))
            else:
                raise FileNotFoundError("Página não encontrada.")
            trilha = trilha_navegacao(c, parsed.path, query)
            status = 200
        except (OSError, ValueError, xlrd.XLRDError) as exc:
            LOG.warning("Não foi possível abrir %s: %s", self.path, exc)
            status = 404 if isinstance(exc, FileNotFoundError) else 400
            conteudo = f"<h1>Não foi possível abrir o conteúdo</h1><p class='error'>{esc(exc)}</p>"
            trilha = renderizar_trilha([("Início", "/"), ("Erro", "")])
        except Exception:
            LOG.exception("Erro ao processar %s", self.path)
            status = 500
            conteudo = "<h1>Erro ao ler o conteúdo</h1><p class='error'>Consulte o terminal para detalhes.</p>"
            trilha = renderizar_trilha([("Início", "/"), ("Erro", "")])
        data = pagina("Leitor", conteudo, identidade, trilha)
        self.responder(status, data, "text/html; charset=utf-8")

    def responder(self, status: int, data: bytes, tipo: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Leia um CD técnico local no navegador.")
    parser.add_argument("--porta", type=int, default=8000, help="porta local (padrão: 8000)")
    parser.add_argument("--abrir", action="store_true", help="abrir o navegador automaticamente")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        fonte = FonteCD(detector_sistema())
        servidor = Servidor(("127.0.0.1", args.porta), fonte)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    endereco = f"http://127.0.0.1:{servidor.server_port}/"
    print(f"Aguardando CD de literatura técnica na unidade óptica.\nAbra {endereco} — Ctrl+C para encerrar.", flush=True)
    if args.abrir:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
