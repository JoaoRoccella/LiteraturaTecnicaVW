import tempfile
import threading
import unittest
import ctypes
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from leitor.__main__ import (
    Servidor, busca, documento, imagem_interativa, planilha, trilha_navegacao, veiculos, vista,
)
from leitor.catalogo import Catalogo, ler_tabela
from leitor.midia import DetectorLinux, DetectorWindows, FonteCD, Midia, Resultado, _montagens


ROOT = Path(__file__).resolve().parents[1]


class DetectorSimulado:
    def __init__(self):
        self.resultado = Resultado((), ("/dev/sr0",))

    def detectar(self):
        return self.resultado


class PontosHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pontos = []

    def handle_starttag(self, tag, attrs):
        if tag == "circle":
            self.pontos.append(dict(attrs))


class TrilhaHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.trilha = False
        self.itens = []
        self.atual = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("aria-label") == "Caminho de navegação":
            self.trilha = True
        if self.trilha and tag == "li":
            self.atual = {"texto": "", "href": None, "current": attrs.get("aria-current")}
        if self.trilha and tag == "a" and self.atual is not None:
            self.atual["href"] = attrs.get("href")

    def handle_data(self, data):
        if self.atual is not None:
            self.atual["texto"] += data

    def handle_endtag(self, tag):
        if tag == "li" and self.atual is not None:
            self.itens.append(self.atual)
            self.atual = None
        if tag == "nav":
            self.trilha = False


def trilha_itens(html):
    parser = TrilhaHTML()
    parser.feed(html)
    return parser.itens


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

    def test_pontos_interativos_e_dados_invalidos(self):
        imagem = self.root / "imagens" / "img" / "pontos.gif"
        imagem.write_bytes(b"GIF89a" + (200).to_bytes(2, "little") + (100).to_bytes(2, "little"))
        linhas = [
            {"NomePeca": 'Peça <A> "X"', "X": "10", "Y": "20", "TipoDoLink": "1", "Link": "1010"},
            {"NomePeca": "Sem link", "X": "60", "Y": "30", "TipoDoLink": "0", "Link": ""},
            {"NomePeca": "Fora", "X": "200", "Y": "1", "TipoDoLink": "1", "Link": "1011"},
            {"NomePeca": "Inválida", "X": "", "Y": "1", "TipoDoLink": "0", "Link": ""},
            {"NomePeca": "Link ausente", "X": "90", "Y": "40", "TipoDoLink": "1", "Link": ""},
        ]
        resultado = imagem_interativa(self.catalogo, imagem, linhas, "dados/Modelo")
        parser = PontosHTML()
        parser.feed(resultado)
        self.assertEqual(len(parser.pontos), 3)
        self.assertEqual((parser.pontos[0]["cx"], parser.pontos[0]["cy"], parser.pontos[0]["r"]), ("25", "35", "23"))
        self.assertEqual(parser.pontos[0]["data-nome"], 'Peça <A> "X"')
        self.assertEqual(parser.pontos[0]["data-destino"], "/buscar?q=1010&escopo=dados%2FModelo&prefixo=1")
        self.assertEqual(parser.pontos[1]["data-destino"], "")
        self.assertEqual(parser.pontos[2]["data-destino"], "")
        self.assertIn("linha 3: ponto fora da imagem", resultado)
        self.assertIn("linha 4: coordenadas inválidas", resultado)
        self.assertIn("linha 5: código de link ausente", resultado)

    def test_imagem_interativa_rejeita_gif_invalido(self):
        imagem = self.root / "imagens" / "img" / "fig1.GIF"
        with self.assertRaisesRegex(ValueError, "Imagem GIF inválida"):
            imagem_interativa(self.catalogo, imagem, [], "")

    def test_trilha_de_pastas_e_arquivo_direto(self):
        caminho = "dados/Modelo/Motor/principal.TXT"
        itens = trilha_itens(trilha_navegacao(self.catalogo, "/arquivo", {"path": caminho}))
        self.assertEqual([item["texto"] for item in itens],
                         ["Início", "Arquivos", "dados", "Modelo", "Motor", "principal.TXT"])
        self.assertEqual(itens[3]["href"], "/pasta?path=dados%2FModelo")
        self.assertEqual(itens[-1]["current"], "page")
        self.assertIsNone(itens[-1]["href"])
        itens = trilha_itens(trilha_navegacao(self.catalogo, "/pasta", {"path": "dados/Modelo"}))
        self.assertEqual(itens[-1]["texto"], "Modelo")
        self.assertEqual(itens[-2]["href"], "/pasta?path=dados")
        self.assertEqual(trilha_itens(trilha_navegacao(self.catalogo, "/", {}))[0]["current"], "page")

    def test_trilha_e_contexto_ate_documento(self):
        tabelas = {
            "dados/Carro.DB": [{"CodCarro": "1", "NCarro": "Gol 95 >"}],
            "dados/Conjuntos.DB": [{"CodConjunto": "1", "NConjunto": "Motor"}],
            "dados/Grupo.DB": [{"CodGrupo": "10", "NGrupo": "Bloco"}],
            "dados/VarGrupo.DB": [{"CodVarGrupo": "4", "NVarGrupo": "AE"}],
        }
        contexto = {"carro": "1", "conjunto": "1", "grupo": "10", "variante": "4", "vista": "10-403"}
        parametros = contexto | {"path": "dados/Modelo/Motor/principal.TXT", "busca_q": "1010",
                                 "busca_escopo": "dados/Modelo", "busca_prefixo": "1", "busca_offset": "200"}
        with patch("leitor.__main__.ler_lista", side_effect=lambda c, path: tabelas[path]):
            itens = trilha_itens(trilha_navegacao(self.catalogo, "/arquivo", parametros))
        self.assertEqual([item["texto"] for item in itens], [
            "Início", "Veículos e vistas", "Veículo: Gol 95 >", "Conjunto: Motor",
            "Grupo: Bloco", "Variante: AE", "Vista 10-403", "Buscar textos",
            "Busca: 1010", "principal.TXT",
        ])
        self.assertEqual(itens[4]["href"], "/veiculos?carro=1&conjunto=1&grupo=10")
        self.assertIn("codigo=10-403", itens[6]["href"])
        self.assertIn("variante=4", itens[6]["href"])
        self.assertIn("offset=200", itens[8]["href"])
        resultado = busca(self.catalogo, "atencao", 0, "dados/Modelo", False, contexto)
        self.assertIn("busca_q=atencao", resultado)
        self.assertIn("vista=10-403", resultado)
        documentos = self.catalogo.indexar_documentos()
        with patch.object(self.catalogo, "indexar_documentos", return_value=documentos * 201):
            resultado = busca(self.catalogo, "atencao", 0, "dados/Modelo", False, contexto)
        self.assertIn("offset=200", resultado)
        self.assertIn("variante=4", resultado)
        resultado = documento(self.catalogo, self.doc, {
            **contexto, "busca_q": "1010", "busca_escopo": "dados/Modelo",
        })
        self.assertIn("busca_q=1010", resultado)
        self.assertIn("vista=10-403", resultado)

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

    def test_loader_responde_durante_leitura_e_preserva_acesso_direto(self):
        iniciou = threading.Event()
        segundo_iniciou = threading.Event()
        liberar = threading.Event()
        detector = DetectorSimulado()
        fonte = FonteCD(detector)
        atualizar = fonte.atualizar

        def leitura_lenta():
            if iniciou.is_set():
                segundo_iniciou.set()
            else:
                iniciou.set()
            if not liberar.wait(5):
                raise TimeoutError("A leitura simulada não foi liberada")
            return atualizar()

        servidor = Servidor(("127.0.0.1", 0), fonte)
        thread = threading.Thread(target=servidor.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{servidor.server_port}"
        respostas = []

        def ler_conteudo():
            requisicao = Request(base + "/buscar?q=abc&escopo=dados", headers={"X-Leitor-Conteudo": "1"})
            with urlopen(requisicao, timeout=5) as resposta:
                respostas.append(resposta.read().decode())

        leitor = threading.Thread(target=ler_conteudo)
        outro_leitor = threading.Thread(target=ler_conteudo)
        try:
            with patch.object(fonte, "atualizar", side_effect=leitura_lenta):
                leitor.start()
                self.assertTrue(iniciou.wait(2))
                requisicao = Request(base + "/buscar?q=abc&escopo=dados",
                                     headers={"Accept": "text/html"})
                with urlopen(requisicao, timeout=2) as resposta:
                    shell = resposta.read().decode()
                self.assertIn("Verificando e lendo dados do CD", shell)
                self.assertIn("/buscar?q=abc&amp;escopo=dados&amp;__direto=1", shell)
                self.assertTrue(leitor.is_alive())
                outro_leitor.start()
                self.assertFalse(segundo_iniciou.wait(0.3))
                liberar.set()
                leitor.join(5)
                outro_leitor.join(5)
                self.assertFalse(leitor.is_alive())
                self.assertFalse(outro_leitor.is_alive())
                self.assertTrue(segundo_iniciou.is_set())
                self.assertEqual(len(respostas), 2)
                self.assertIn("Insira o CD", respostas[0])
            requisicao = Request(base + "/buscar?q=abc&escopo=dados&__direto=1",
                                 headers={"Accept": "text/html"})
            with urlopen(requisicao, timeout=5) as resposta:
                self.assertIn("Insira o CD", resposta.read().decode())
        finally:
            liberar.set()
            if leitor.ident is not None:
                leitor.join(5)
            if outro_leitor.ident is not None:
                outro_leitor.join(5)
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
        self.assertIn("grupo=10&amp;variante=4", veiculos(self.catalogo, params))
        resultado = vista(self.catalogo, "10-403", "1")
        self.assertIn("imagens%2Fve", resultado)
        self.assertIn("prefixo=1", resultado)
        self.assertIn("dados%2FGol", resultado)
        parser = PontosHTML()
        parser.feed(resultado)
        bloco = next(p for p in parser.pontos if p["data-nome"] == "Bloco do Motor")
        self.assertEqual((bloco["cx"], bloco["cy"]), ("486", "507"))
        self.assertIn("q=1010", bloco["data-destino"])
        itens = trilha_itens(trilha_navegacao(self.catalogo, "/vista", params | {"codigo": "10-403"}))
        self.assertEqual(itens[-1]["texto"], "Vista 10-403")
        self.assertEqual(itens[-2]["href"], "/veiculos?carro=1&conjunto=1&grupo=10&variante=4")

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
