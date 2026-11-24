from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from paradoxdb import open_table


def normalizar(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(c)
    )


def ler_texto(path: Path) -> str:
    return path.read_bytes().decode("cp1252", errors="replace")


@dataclass(frozen=True)
class Documento:
    caminho: Path
    titulo: str
    busca: str


class Catalogo:
    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve(strict=True)
        if not self.raiz.is_dir():
            raise ValueError("A raiz do CD deve conter a pasta 'dados'.")
        pastas_dados = [p for p in self.raiz.iterdir()
                        if p.name.casefold() == "dados" and p.is_dir() and not p.is_symlink()]
        if len(pastas_dados) != 1:
            raise ValueError("A raiz do CD deve conter uma única pasta 'dados'.")
        self.dados = pastas_dados[0]
        self.arquivos: dict[str, Path] = {}
        self.diretorios: dict[str, Path] = {"": self.raiz}
        self.por_nome: dict[str, list[Path]] = defaultdict(list)
        self.documentos: list[Documento] | None = None
        self.erros_indexacao: list[str] = []
        def erro_caminhada(erro: OSError) -> None:
            raise erro

        for pasta, diretorios, arquivos in os.walk(self.raiz, followlinks=False, onerror=erro_caminhada):
            pasta_atual = Path(pasta)
            self.diretorios[self.relativo(pasta_atual).casefold()] = pasta_atual
            diretorios[:] = [
                d for d in diretorios
                if d not in {".venv", "__pycache__", ".git", "leitor", "tests"}
                and not (Path(pasta) / d).is_symlink()
            ]
            for nome in arquivos:
                path = Path(pasta) / nome
                if path.is_symlink():
                    continue
                rel = path.relative_to(self.raiz)
                self.arquivos[str(rel).replace("\\", "/").casefold()] = path
                self.por_nome[nome.casefold()].append(path)

    def caminho(self, relativo: str) -> Path:
        path = (self.raiz / relativo.replace("\\", "/")).resolve()
        if not path.is_relative_to(self.raiz):
            raise ValueError("O caminho está fora da raiz do CD.")
        return self.diretorios.get(self.relativo(path).casefold(), path)

    def relativo(self, path: Path) -> str:
        return path.relative_to(self.raiz).as_posix()

    def localizar(self, relativo: str) -> Path | None:
        return self.arquivos.get(relativo.replace("\\", "/").lstrip("/").casefold())

    def referencias(self, origem: Path, nome: str) -> list[Path]:
        nome = nome.strip().replace("\\", "/")
        if not nome or nome.startswith("/") or ".." in Path(nome).parts:
            return []
        caminhos = [origem.parent / nome, origem.parent / "auxi" / nome]
        sufixo = Path(nome).suffix.casefold()
        for base in ("imagens/img", "imagens/ve", "imagens/esq", "dados/Tabelas"):
            caminhos.append(self.raiz / base / nome)
            if sufixo == ".bmp":
                caminhos.append(self.raiz / base / str(Path(nome).with_suffix(".gif")))
        for caminho in caminhos:
            try:
                rel = self.relativo(caminho)
            except ValueError:
                continue
            encontrado = self.localizar(rel)
            if encontrado:
                return [encontrado]
        candidatos = self.por_nome.get(Path(nome).name.casefold(), [])
        if not candidatos and sufixo == ".bmp":
            candidatos = self.por_nome.get(Path(nome).with_suffix(".gif").name.casefold(), [])
        if origem.is_relative_to(self.dados):
            partes = origem.relative_to(self.dados).parts
            if len(partes) > 1:
                pasta_veiculo = self.dados / partes[0]
                locais = [p for p in candidatos if p.is_relative_to(pasta_veiculo)]
                if locais:
                    return locais
        return candidatos

    def indexar_documentos(self) -> list[Documento]:
        if self.documentos is not None:
            return self.documentos
        documentos: list[Documento] = []
        for path in self.arquivos.values():
            if path.suffix.casefold() != ".txt" or not path.is_relative_to(self.dados):
                continue
            try:
                texto = ler_texto(path)
            except OSError as exc:
                self.erros_indexacao.append(f"{self.relativo(path)}: {exc}")
                continue
            match = re.search(r"(?im)^\s*title\s*=\s*(.*)$", texto)
            titulo = match.group(1).strip() if match else path.stem
            documentos.append(Documento(path, titulo, normalizar(f"{self.relativo(path)}\n{texto}")))
        self.documentos = sorted(documentos, key=lambda item: (normalizar(item.titulo), str(item.caminho)))
        return self.documentos


def ler_tabela(path: Path) -> tuple[list[str], list[dict[str, str]], str | None]:
    tabela = open_table(path)
    campos = [campo.name for campo in tabela.header.fields]
    linhas = []
    for registro in tabela.records():
        linha = {}
        for campo in campos:
            celula = registro[campo]
            if celula.is_ok:
                valor = celula.value
                linha[campo] = valor.hex() if isinstance(valor, bytes) else str(valor)
            elif str(celula.state) == "NULL":
                linha[campo] = ""
            else:
                linha[campo] = f"[INVÁLIDO: {celula.reason}; dados: {celula.raw_bytes.hex()}]"
        linhas.append(linha)
    aviso = None
    if tabela.header_mismatch:
        aviso = f"Cabeçalho declara {tabela.declared_record_count} registros; foram lidos {len(linhas)}."
    return campos, linhas, aviso
