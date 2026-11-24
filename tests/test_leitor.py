import tempfile
import threading
import unittest
import ctypes
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from leitor.__main__ import Servidor, busca, documento, planilha, veiculos, vista
from leitor.catalogo import Catalogo, ler_tabela
from leitor.midia import DetectorLinux, DetectorWindows, FonteCD, Midia, Resultado, _montagens


ROOT = Path(__file__).resolve().parents[1]


class DetectorSimulado:
    def __init__(self):
        self.resultado = Resultado((), ("/dev/sr0",))

    def detectar(self):
        return self.resultado


class CatalogoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "dados" / "Modelo" / "Motor" / "auxi").mkdir(parents=True)
        (self.root / "dados" / "Tabelas").mkdir()
        for nome in ("Carro.DB", "Conjuntos.DB", "Grupo.DB", "VarGrupo.DB"):
            (self.root / "dados" / nome).touch()
        (self.root / "imagens" / "img").mkdir(parents=True)
        self.doc = self.root / "dados" / "Modelo" / "Motor" / "principal.TXT"
        self.doc.write_bytes(
            "title=Operação <teste>\n&Chave\nTorquímetro&\n#Passo\n@Atenção\n"
            "$filename=SUB.TXT$\n%filename=figura.bmp%\n%filename=ausente.bmp%\n".encode("cp1252")
        )
        (self.doc.parent / "auxi" / "sub.txt").write_bytes(b"title=Secundario\n#Outra linha")
        (self.root / "dados" / "Tabelas" / "figura.vf").write_text("fig1\nfig2\n")
        (self.root / "imagens" / "img" / "fig1.GIF").write_bytes(b"GIF89a")
        (self.root / "imagens" / "img" / "fig2.gif").write_bytes(b"GIF89a")
        (self.root / "imagens" / "img" / "original.bmp").write_bytes(b"BM")
        self.catalogo = Catalogo(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_documento_referencias_e_marcadores(self):
        resultado = documento(self.catalogo, self.doc)
        self.assertIn("Operação &lt;teste&gt;", resultado)
        self.assertIn("Torquímetro", resultado)
        self.assertIn("Atenção", resultado)
        self.assertIn("auxi%2Fsub.txt", resultado)
        self.assertIn("fig1.GIF", resultado)
        self.assertIn("fig2.gif", resultado)
        self.assertIn("Referência não encontrada: ausente.bmp", resultado)

    def test_busca_sem_acentos_escopo_e_prefixo(self):
        self.assertIn("Operação &lt;teste&gt;", busca(self.catalogo, "atencao", 0))
        self.assertIn("Secundario", busca(self.catalogo, "sub", 0, "dados/Modelo", True))
        self.assertNotIn("Secundario", busca(self.catalogo, "sub", 0, "dados/Tabelas", True))
        with self.assertRaises(ValueError):
            busca(self.catalogo, "sub", 0, "../", True)

    def test_referencia_nao_escapa_da_raiz(self):
        self.assertEqual(self.catalogo.referencias(self.doc, "../../secreto.txt"), [])
        self.assertEqual(self.catalogo.referencias(self.doc, "original.bmp")[0].name, "original.bmp")
        with self.assertRaises(ValueError):
            self.catalogo.caminho("../secreto.txt")

    def test_pasta_dados_com_caixa_diferente(self):
        (self.root / "dados").rename(self.root / "DADOS")
        catalogo = Catalogo(self.root)
        self.assertEqual(catalogo.caminho("dados/modelo"), self.root / "DADOS" / "Modelo")
        self.assertIn("Operação", busca(catalogo, "atencao", 0))

    def test_http_rotas_e_arquivos_fora_da_raiz(self):
        detector = DetectorSimulado()
        midia = Midia(self.root, "/dev/sr0", "disco-1")
        detector.resultado = Resultado((midia,), (midia.dispositivo,))
        servidor = Servidor(("127.0.0.1", 0), FonteCD(detector))
        thread = threading.Thread(target=servidor.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{servidor.server_port}"
        try:
            with urlopen(base + "/arquivo?" + urlencode({"path": self.catalogo.relativo(self.doc)})) as resposta:
                self.assertEqual(resposta.status, 200)
                self.assertIn("Operação", resposta.read().decode())
            with self.assertRaises(HTTPError) as erro:
                urlopen(base + "/pasta?" + urlencode({"path": "../"}))
            self.assertEqual(erro.exception.code, 400)
            erro.exception.close()
            with self.assertRaises(HTTPError) as erro:
                urlopen(base + "/imagem?" + urlencode({"path": self.catalogo.relativo(self.doc)}))
            self.assertEqual(erro.exception.code, 400)
            erro.exception.close()
        finally:
            servidor.shutdown()
            thread.join()
            servidor.server_close()

    def test_insercao_remocao_e_troca_de_midia(self):
        detector = DetectorSimulado()
        fonte = FonteCD(detector)
        self.assertIsNone(fonte.atualizar())
        self.assertIn("Insira", fonte.mensagem)

        primeira = Midia(self.root, "/dev/sr0", "montagem-1")
        detector.resultado = Resultado((primeira,), (primeira.dispositivo,))
        catalogo = fonte.atualizar()
        self.assertIsNotNone(catalogo)
        self.assertIs(catalogo, fonte.atualizar())

        segunda = Midia(self.root, "/dev/sr0", "montagem-2")
        detector.resultado = Resultado((segunda,), (segunda.dispositivo,))
        self.assertIsNot(catalogo, fonte.atualizar())
        self.assertEqual(fonte.midia, segunda)

        detector.resultado = Resultado((), ("/dev/sr0",))
        self.assertIsNone(fonte.atualizar())
        self.assertIsNone(fonte.catalogo)

    def test_remocao_durante_catalogacao_nao_publica_resultado(self):
        midia = Midia(self.root, "/dev/sr0", "montagem-1")

        class DetectorTroca:
            chamadas = 0

            def detectar(self):
                self.chamadas += 1
                return Resultado((midia,) if self.chamadas == 1 else (), ("/dev/sr0",))

        fonte = FonteCD(DetectorTroca())
        self.assertIsNone(fonte.atualizar())
        self.assertIsNone(fonte.catalogo)
        self.assertIn("removido ou alterado", fonte.mensagem)

    def test_cd_incompativel_e_erro_de_leitura(self):
        detector = DetectorSimulado()
        fonte = FonteCD(detector)
        midia = Midia(self.root / "imagens", "/dev/sr0", "incompatível")
        detector.resultado = Resultado((midia,), (midia.dispositivo,))
        self.assertIsNone(fonte.atualizar())
        self.assertIn("incompatível", fonte.mensagem)
        midia = Midia(self.root / "ausente", "/dev/sr0", "não legível")
        detector.resultado = Resultado((midia,), (midia.dispositivo,))
        self.assertIsNone(fonte.atualizar())
        self.assertIn("não foi possível ler", fonte.mensagem)

    def test_tela_de_espera_e_insercao_por_http(self):
        detector = DetectorSimulado()
        servidor = Servidor(("127.0.0.1", 0), FonteCD(detector))
        thread = threading.Thread(target=servidor.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{servidor.server_port}"
        try:
            with urlopen(base + "/") as resposta:
                self.assertIn("Insira o CD", resposta.read().decode())
            with self.assertRaises(HTTPError) as erro:
                urlopen(base + "/imagem?path=imagens%2Fimg%2Ffig1.GIF")
            self.assertEqual(erro.exception.code, 503)
            erro.exception.close()
            midia = Midia(self.root, "/dev/sr0", "novo-cd")
            detector.resultado = Resultado((midia,), (midia.dispositivo,))
            with urlopen(base + "/status") as resposta:
                self.assertIn('"midia": "novo-cd"', resposta.read().decode())
            with urlopen(base + "/") as resposta:
                self.assertIn("Literatura técnica automotiva", resposta.read().decode())
            detector.resultado = Resultado((), (midia.dispositivo,))
            with urlopen(base + "/status") as resposta:
                self.assertIn('"midia": ""', resposta.read().decode())
            with urlopen(base + "/arquivo?path=dados%2FModelo%2FMotor%2Fprincipal.TXT") as resposta:
                self.assertIn("Insira o CD", resposta.read().decode())
        finally:
            servidor.shutdown()
            thread.join()
            servidor.server_close()

    def test_montagens_escapadas_e_identificacao(self):
        texto = "42 1 11:0 / /media/CD\\040Volks ro - iso9660 /dev/sr0 ro\n"
        self.assertEqual(_montagens(texto), [("42", "11:0", "/", "/media/CD Volks", "iso9660")])

    def test_detector_linux_montagem_e_tentativa_de_montar(self):
        sysfs = self.root / "sys"
        unidade = sysfs / "sr0"
        (unidade / "device").mkdir(parents=True)
        (unidade / "device" / "type").write_text("5")
        (unidade / "dev").write_text("11:0")
        (unidade / "size").write_text("1234")
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text("42 1 11:0 / /media/CD\\040Volks ro - iso9660 /dev/sr0 ro\n")
        detector = DetectorLinux(sysfs, mountinfo)
        resultado = detector.detectar()
        self.assertEqual(resultado.unidades, ("/dev/sr0",))
        self.assertEqual(resultado.midias[0].raiz, Path("/media/CD Volks"))

        mountinfo.write_text("")
        with patch("leitor.midia.subprocess.run") as executar:
            executar.return_value.returncode = 1
            executar.return_value.stderr = "permissão negada"
            executar.return_value.stdout = ""
            resultado = detector.detectar()
            self.assertIn("permissão negada", resultado.avisos[0])
            self.assertIn("ro", executar.call_args.args[0])
            self.assertIn("--no-user-interaction", executar.call_args.args[0])
            detector.detectar()
            executar.assert_called_once()

        (unidade / "size").write_text("0")
        self.assertEqual(detector.detectar().avisos, ())

    def test_detector_windows_identifica_unidade_e_serial(self):
        class Kernel:
            def SetThreadErrorMode(self, modo, anterior):
                if anterior is not None:
                    ctypes.cast(anterior, ctypes.POINTER(ctypes.c_uint32))[0] = 0
                return 1

            def GetDriveTypeW(self, raiz):
                return 5 if raiz in ("D:\\", "E:\\") else 3

            def GetVolumeInformationW(self, raiz, _nome, _tamanho, serial, *_resto):
                if raiz == "E:\\":
                    return 0
                ctypes.cast(serial, ctypes.POINTER(ctypes.c_uint32))[0] = 42
                return 1

        class Windll:
            kernel32 = Kernel()

        with patch("leitor.midia.ctypes.windll", Windll(), create=True):
            resultado = DetectorWindows().detectar()
        self.assertEqual(resultado.unidades, ("D:\\", "E:\\"))
        self.assertEqual(resultado.midias[0].identidade, "windows:D:\\:42")
        self.assertIn("insira", resultado.avisos[0])


@unittest.skipUnless((ROOT / "dados" / "Carro.DB").exists(), "CD de exemplo não disponível")
class CDRealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogo = Catalogo(ROOT)

    def test_paradox_e_navegacao_completa(self):
        campos, linhas, aviso = ler_tabela(ROOT / "dados" / "Carro.DB")
        self.assertEqual(campos, ["CodCarro", "NCarro"])
        self.assertIn({"CodCarro": "1", "NCarro": "Gol 95 >"}, linhas)
        self.assertIsNone(aviso)
        params = {}
        for nivel, valor, esperado in [
            ("carro", "1", "Gol 95"),
            ("conjunto", "1", "Motor"),
            ("grupo", "10", "Cilindro"),
            ("variante", "4", "AE"),
        ]:
            self.assertIn(esperado, veiculos(self.catalogo, params))
            params[nivel] = valor
        self.assertIn("10-403", veiculos(self.catalogo, params))
        resultado = vista(self.catalogo, "10-403", "1")
        self.assertIn("imagens%2Fve", resultado)
        self.assertIn("prefixo=1", resultado)
        self.assertIn("dados%2FGol", resultado)

    def test_planilha_e_texto_com_imagens(self):
        resultado = documento(self.catalogo, ROOT / "dados" / "GolIII" / "Mp90" / "248855K.TXT")
        self.assertIn("Sensor integrado", resultado)
        self.assertIn("24-319.GIF", resultado)
        self.assertIn("242519K.TXT", resultado)
        resultado = planilha(self.catalogo, ROOT / "dados" / "Tabelas" / "1000dto.xls", 0)
        self.assertIn("<table>", resultado)
        self.assertIn("Plan1", resultado)


if __name__ == "__main__":
    unittest.main()
