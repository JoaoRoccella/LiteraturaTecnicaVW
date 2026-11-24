from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .catalogo import Catalogo


LOG = logging.getLogger("leitor.midia")


@dataclass(frozen=True)
class Midia:
    raiz: Path
    dispositivo: str
    identidade: str


@dataclass(frozen=True)
class Resultado:
    midias: tuple[Midia, ...]
    unidades: tuple[str, ...]
    avisos: tuple[str, ...] = ()


class Detector(Protocol):
    def detectar(self) -> Resultado: ...


def _desescapar(valor: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), valor)


def _montagens(texto: str) -> list[tuple[str, str, str, str, str]]:
    resultado = []
    for linha in texto.splitlines():
        antes, separador, depois = linha.partition(" - ")
        campos, finais = antes.split(), depois.split()
        if not separador or len(campos) < 6 or len(finais) < 2:
            raise ValueError("Informações de montagem do Linux inválidas.")
        resultado.append((campos[0], campos[2], _desescapar(campos[3]),
                          _desescapar(campos[4]), finais[0]))
    return resultado


class DetectorLinux:
    def __init__(self, sysfs: Path = Path("/sys/class/block"),
                 mountinfo: Path = Path("/proc/self/mountinfo")) -> None:
        self.sysfs = sysfs
        self.mountinfo = mountinfo
        self.ultima_tentativa: dict[str, float] = {}
        self.erros_montagem: dict[str, str] = {}

    def detectar(self) -> Resultado:
        montagens = _montagens(self.mountinfo.read_text(encoding="utf-8", errors="surrogateescape"))
        unidades = []
        midias = []
        avisos = []
        for dispositivo in sorted(self.sysfs.iterdir()):
            tipo = dispositivo / "device/type"
            if not tipo.exists() or tipo.read_text().strip() != "5":
                continue
            nome = f"/dev/{dispositivo.name}"
            unidades.append(nome)
            numero = (dispositivo / "dev").read_text().strip()
            montadas = [m for m in montagens if m[1] == numero and m[2] == "/"
                         and m[4] in {"iso9660", "udf"}]
            for identificador, _, _, ponto, _ in montadas:
                midias.append(Midia(Path(ponto), nome, f"linux:{numero}:{identificador}:{ponto}"))
            if montadas:
                self.erros_montagem.pop(nome, None)
            elif int((dispositivo / "size").read_text().strip()) > 0:
                agora = time.monotonic()
                if agora - self.ultima_tentativa.get(nome, float("-inf")) >= 15:
                    self.ultima_tentativa[nome] = agora
                    try:
                        processo = subprocess.run(
                            ["udisksctl", "mount", "--block-device", nome, "--options", "ro",
                             "--no-user-interaction"],
                            capture_output=True, text=True, errors="replace", timeout=10, check=False,
                        )
                        if processo.returncode:
                            self.erros_montagem[nome] = processo.stderr.strip() or processo.stdout.strip() or "erro de montagem"
                        else:
                            self.erros_montagem.pop(nome, None)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        self.erros_montagem[nome] = str(exc)
                if nome in self.erros_montagem:
                    avisos.append(f"{nome}: não foi possível montar o CD: {self.erros_montagem[nome]}")
                else:
                    avisos.append(f"{nome}: aguardando a montagem do CD.")
            else:
                self.erros_montagem.pop(nome, None)
                self.ultima_tentativa.pop(nome, None)
        return Resultado(tuple(midias), tuple(unidades), tuple(avisos))


class DetectorWindows:
    def detectar(self) -> Resultado:
        kernel = ctypes.windll.kernel32
        unidades = []
        midias = []
        avisos = []
        modo_anterior = ctypes.c_uint32()
        if not kernel.SetThreadErrorMode(1, ctypes.byref(modo_anterior)):
            raise OSError("Não foi possível configurar a detecção de unidades no Windows.")
        try:
            for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                raiz = f"{letra}:\\"
                if kernel.GetDriveTypeW(raiz) != 5:
                    continue
                unidades.append(raiz)
                serial = ctypes.c_uint32()
                if kernel.GetVolumeInformationW(raiz, None, 0, ctypes.byref(serial), None, None, None, 0):
                    midias.append(Midia(Path(raiz), raiz, f"windows:{raiz}:{serial.value}"))
                else:
                    avisos.append(f"{raiz}: insira um CD legível na unidade.")
        finally:
            kernel.SetThreadErrorMode(modo_anterior.value, None)
        return Resultado(tuple(midias), tuple(unidades), tuple(avisos))


def detector_sistema() -> DetectorLinux | DetectorWindows:
    if sys.platform.startswith("linux"):
        return DetectorLinux()
    if sys.platform == "win32":
        return DetectorWindows()
    raise RuntimeError("Detecção de unidades ópticas disponível apenas em Linux e Windows.")


def compativel(raiz: Path) -> bool:
    dados = [p for p in raiz.iterdir() if p.name.casefold() == "dados" and p.is_dir()]
    if len(dados) != 1:
        return False
    nomes = {p.name.casefold() for p in dados[0].iterdir() if p.is_file()}
    return {"carro.db", "conjuntos.db", "grupo.db", "vargrupo.db"} <= nomes


class FonteCD:
    def __init__(self, detector: Detector) -> None:
        self.detector = detector
        self.midia: Midia | None = None
        self.catalogo: Catalogo | None = None
        self.mensagem = "Insira um CD de literatura técnica na unidade óptica."

    def atualizar(self) -> Catalogo | None:
        try:
            resultado = self.detector.detectar()
        except (OSError, ValueError) as exc:
            self.midia = None
            self.catalogo = None
            self.mensagem = f"Não foi possível detectar as unidades ópticas: {exc}"
            LOG.warning("%s", self.mensagem)
            return None
        candidatas = []
        avisos = list(resultado.avisos)
        for midia in resultado.midias:
            try:
                if compativel(midia.raiz):
                    candidatas.append(midia)
                else:
                    avisos.append(f"{midia.dispositivo}: CD incompatível; faltam as tabelas principais em dados.")
            except OSError as exc:
                avisos.append(f"{midia.dispositivo}: não foi possível ler o CD: {exc}")

        atual = next((m for m in candidatas if m == self.midia), None)
        escolhida = atual or (candidatas[0] if candidatas else None)
        if escolhida != self.midia:
            self.midia = None
            self.catalogo = None
            if escolhida:
                LOG.info("Lendo CD em %s (%s)", escolhida.dispositivo, escolhida.raiz)
                try:
                    catalogo = Catalogo(escolhida.raiz)
                    # Não publicar um catálogo se a mídia mudou durante a leitura.
                    if escolhida in self.detector.detectar().midias:
                        self.midia, self.catalogo = escolhida, catalogo
                    else:
                        avisos.append("O CD foi removido ou alterado durante a leitura.")
                except (OSError, ValueError) as exc:
                    LOG.warning("Não foi possível catalogar %s: %s", escolhida.dispositivo, exc)
                    avisos.append(f"{escolhida.dispositivo}: não foi possível catalogar o CD: {exc}")
        if self.catalogo is None:
            unidades = ", ".join(resultado.unidades) if resultado.unidades else "nenhuma unidade óptica encontrada"
            self.mensagem = f"Insira um CD de literatura técnica. Unidades: {unidades}."
            if avisos:
                self.mensagem += " " + " ".join(avisos)
        elif self.midia:
            self.mensagem = f"CD disponível em {self.midia.dispositivo}: {self.midia.raiz}."
        return self.catalogo
