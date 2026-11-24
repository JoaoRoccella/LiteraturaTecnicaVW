from __future__ import annotations

import argparse
import html
import json
import logging
import mimetypes
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import xlrd

from .catalogo import Catalogo, ler_tabela, ler_texto, normalizar
from .midia import FonteCD, detector_sistema


LOG = logging.getLogger("leitor")
PAGE_SIZE = 200
STYLE = """
:root{font:16px/1.5 system-ui,sans-serif;color:#1c2936;background:#f2f5f7}
*{box-sizing:border-box}body{margin:0}header{background:#173449;color:white;padding:1rem 2rem}
header a{color:white;text-decoration:none}header strong{font-size:1.3rem}nav{display:flex;gap:1.2rem;flex-wrap:wrap;margin-top:.5rem}
main{max-width:1200px;margin:2rem auto;padding:0 1.2rem}a{color:#075b85}a:hover{text-decoration:none}
.card{background:white;border:1px solid #d5e0e6;border-radius:8px;padding:1.1rem;margin:1rem 0;overflow:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:1rem}.grid .card{margin:0}
h1,h2,h3{line-height:1.2}h1{font-size:1.7rem}h2{font-size:1.25rem}
form{display:flex;gap:.5rem;flex-wrap:wrap}input,button,select{font:inherit;padding:.45rem .7rem;border:1px solid #aebfc8;border-radius:4px}
input[type=search]{flex:1;min-width:220px}button{background:#075b85;color:white;cursor:pointer}
table{border-collapse:collapse;font-size:.92rem}th,td{border:1px solid #d5e0e6;padding:.4rem;vertical-align:top;text-align:left;white-space:pre-wrap}
th{background:#e8f0f3}img{max-width:100%;height:auto}.figure{margin:1rem 0}.muted{color:#526774}
.warning{padding:.8rem;background:#fff2cf;border-left:4px solid #cc9000}.error{background:#ffe5e5;border-left:4px solid #c33;padding:1rem}
.path{overflow-wrap:anywhere}.doc p{margin:.5rem 0}.doc .nota{border-left:3px solid #78a5ba;padding-left:.8rem}
.pagination{display:flex;gap:1rem;margin:1rem 0}.files{columns:2;column-width:300px;padding-left:1.4rem}
.vista-svg{display:block;width:100%;height:auto}
.vista-ponto{fill:transparent;stroke:transparent;stroke-width:3;cursor:pointer}
.vista-ponto:hover,.vista-ponto:focus-visible,.vista-ponto[aria-pressed=true]{stroke:#075b85;outline:none}
.breadcrumb ol{display:flex;flex-wrap:wrap;gap:.3rem;list-style:none;padding:0;margin:0 0 1.5rem}
.breadcrumb li:not(:first-child)::before{content:"/";color:#526774;margin-right:.3rem}
.breadcrumb li{overflow-wrap:anywhere}
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def url(route: str, **params: object) -> str:
    return route + ("?" + urlencode(params) if params else "")


def link(route: str, label: object, **params: object) -> str:
    return f'<a href="{esc(url(route, **params))}">{esc(label)}</a>'


def arquivo_link(catalogo: Catalogo, path: Path, label: str | None = None,
                 contexto: dict[str, str] | None = None) -> str:
    return link("/arquivo", label or path.name, path=catalogo.relativo(path), **(contexto or {}))


def pagina(titulo: str, conteudo: str, identidade: str = "", trilha: str = "") -> bytes:
    identificador = json.dumps(identidade).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(titulo)} · CD técnico</title>
<style>{STYLE}</style></head><body><header><a href="/"><strong>CD técnico</strong></a>
<nav><a href="/veiculos">Veículos e vistas</a><a href="/buscar">Buscar textos</a>
<a href="/pasta?path=dados">Dados</a><a href="/pasta?path=imagens">Imagens</a>
<a href="/pasta">Arquivos</a></nav></header><main>{trilha}{conteudo}</main>
<script>
const midiaInicial = {identificador};
async function verificarMidia() {{
  try {{
    const resposta = await fetch('/status', {{cache: 'no-store'}});
    const estado = await resposta.json();
    if (estado.midia !== midiaInicial) window.location.replace('/');
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


def imagem_interativa(c: Catalogo, imagem: Path, linhas: list[dict[str, str]], escopo: str,
                      contexto: dict[str, str] | None = None) -> str:
    with imagem.open("rb") as arquivo_imagem:
        cabecalho = arquivo_imagem.read(10)
    if len(cabecalho) != 10 or cabecalho[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError(f"Imagem GIF inválida: {c.relativo(imagem)}")
    largura = int.from_bytes(cabecalho[6:8], "little")
    altura = int.from_bytes(cabecalho[8:10], "little")
    if not largura or not altura:
        raise ValueError(f"Dimensões inválidas na imagem: {c.relativo(imagem)}")

    pontos = []
    problemas = []
    for indice, linha in enumerate(linhas, 1):
        nome = linha.get("NomePeca", "")
        try:
            x, y = int(linha["X"]), int(linha["Y"])
        except (KeyError, ValueError):
            problemas.append(f"linha {indice}: coordenadas inválidas")
            continue
        # As coordenadas do CD são a origem da área de 45 px do programa original.
        centro_x, centro_y = x + 15, y + 15
        if not (0 <= centro_x < largura and 0 <= centro_y < altura):
            problemas.append(f"linha {indice}: ponto fora da imagem")
            continue
        if not nome:
            problemas.append(f"linha {indice}: nome da peça ausente")
            continue
        destino = ""
        if linha.get("TipoDoLink") == "1":
            codigo = linha.get("Link", "")
            if codigo:
                destino = url("/buscar", q=codigo, escopo=escopo, prefixo=1, **(contexto or {}))
            else:
                problemas.append(f"linha {indice}: código de link ausente")
        pontos.append(
            f'<circle class="vista-ponto" cx="{centro_x}" cy="{centro_y}" r="23" '
            f'role="button" tabindex="0" aria-pressed="false" aria-label="Selecionar peça: {esc(nome)}" '
            f'data-nome="{esc(nome)}" data-destino="{esc(destino)}"><title>{esc(nome)}</title></circle>'
        )
    aviso = ""
    if problemas:
        aviso = f'<p class="warning">Não foi possível mapear alguns dados: {esc("; ".join(problemas))}.</p>'
    src = url("/imagem", path=c.relativo(imagem))
    return f"""<div class="card">
<p>Selecione um círculo da imagem para identificar a peça. Use Tab e Enter ou Espaço pelo teclado.</p>
<p id="peca-selecionada" aria-live="polite">Nenhuma peça selecionada.</p>{aviso}
<svg class="vista-svg" style="max-width:{largura}px" viewBox="0 0 {largura} {altura}"
     role="group" aria-label="Vista explodida com peças selecionáveis">
<image href="{esc(src)}" width="{largura}" height="{altura}" />
{''.join(pontos)}</svg></div>
<script>
const pontosVista = document.querySelectorAll('.vista-ponto');
const imagemVista = document.querySelector('.vista-svg');
function selecionarPeca(ponto) {{
  pontosVista.forEach(item => item.setAttribute('aria-pressed', String(item === ponto)));
  const destino = ponto.dataset.destino;
  const painel = document.getElementById('peca-selecionada');
  painel.replaceChildren();
  if (destino) {{
    const nome = document.createElement('a');
    nome.href = destino;
    nome.textContent = ponto.dataset.nome;
    painel.append(nome);
  }} else {{
    painel.textContent = ponto.dataset.nome;
  }}
}}
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
    if imagem and db:
        _, linhas, _ = ler_tabela(db)
        corpo += imagem_interativa(c, imagem, linhas, escopo, origem)
    elif imagem:
        corpo += f'<div class="card"><img src="{esc(url("/imagem", path=c.relativo(imagem)))}" alt="Vista {esc(codigo)}"></div>'
    else:
        corpo += "<p class='warning'>Imagem da vista não encontrada.</p>"
    if db:
        corpo += tabela(c, db, "", 0, escopo, origem)
    else:
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


class Servidor(HTTPServer):
    def __init__(self, address: tuple[str, int], fonte: FonteCD):
        super().__init__(address, Requisicao)
        self.fonte = fonte


class Requisicao(BaseHTTPRequestHandler):
    server: Servidor

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        fonte = self.server.fonte
        identidade = ""
        trilha = ""
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
