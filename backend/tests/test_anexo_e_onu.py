"""Anexo de chamado servido com segurança e associação de ONU à conexão certa."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.sessions import TechnicianSession


def sessao():
    return TechnicianSession(session_id="s", username="joao", controllr_cookie="c", created_at=0)


@pytest.fixture
def cliente():
    from app.deps import get_current_session
    from app.main import app

    app.dependency_overrides[get_current_session] = sessao
    yield TestClient(app)
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ anexo


class _RespostaFalsa:
    def __init__(self, tipo, status=200, conteudo=b"<script>alert(1)</script>"):
        self.status, self.headers, self.conteudo = status, {"Content-Type": tipo}, conteudo

    async def read(self):
        return self.conteudo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _controllr_devolve(monkeypatch, tipo, urls=None, conteudo=b"<script>alert(1)</script>"):
    from app.routers import suporte

    class _Sessao:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, headers=None):
            if urls is not None:
                urls.append(url)
            return _RespostaFalsa(tipo, conteudo=conteudo)

    monkeypatch.setattr(suporte.aiohttp, "ClientSession", _Sessao)


@pytest.mark.parametrize("tipo", ["text/html; charset=utf-8", "image/svg+xml", "application/xhtml+xml", "text/plain"])
def test_anexo_perigoso_vira_download(cliente, monkeypatch, tipo):
    _controllr_devolve(monkeypatch, tipo)
    r = cliente.get("/suporte/tickets/5/anexos/pagina.html")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("tipo", ["image/jpeg", "video/mp4", "audio/mpeg", "application/pdf"])
def test_anexo_de_midia_abre_na_tela(cliente, monkeypatch, tipo):
    _controllr_devolve(monkeypatch, tipo)
    r = cliente.get("/suporte/tickets/5/anexos/foto.jpg")
    assert r.headers["content-type"].startswith(tipo)
    assert "content-disposition" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (b"\xff\xd8\xff\xe0foto", "image/jpeg"),
        (b"\x89PNG\x0d\x0a\x1a\x0a", "image/png"),
        (b"%PDF-1.7", "application/pdf"),
        (b"\x00\x00\x00\x18ftypmp42", "video/mp4"),
    ],
)
def test_midia_sem_tipo_certo_abre_pela_assinatura(cliente, monkeypatch, conteudo, esperado):
    _controllr_devolve(monkeypatch, "application/octet-stream", conteudo=conteudo)
    r = cliente.get("/suporte/tickets/5/anexos/foto.jpg")
    assert r.headers["content-type"].startswith(esperado)
    assert "content-disposition" not in r.headers


@pytest.mark.parametrize("conteudo", [b"<html><script>", b"<svg xmlns=", b"\x00\x00\x00\x18ftypheic"])
def test_sem_tipo_e_sem_assinatura_de_midia_vira_download(cliente, monkeypatch, conteudo):
    _controllr_devolve(monkeypatch, "application/octet-stream", conteudo=conteudo)
    r = cliente.get("/suporte/tickets/5/anexos/foto.jpg")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")


def test_nome_do_anexo_vai_codificado(cliente, monkeypatch):
    urls = []
    _controllr_devolve(monkeypatch, "image/png", urls)
    cliente.get("/suporte/tickets/5/anexos/foto%20da%20cto%3F.png")
    assert urls == ["https://controllr.provedorsupersonic.com.br:8443/support_ctl/annex/view/5/foto%20da%20cto%3F.png"]
    assert cliente.get("/suporte/tickets/5/anexos/..").status_code in (400, 404)


# ------------------------------------------------------------------ ONU


PAYLOAD = {"client_pk": 10, "cpe_pk": 20, "olt_pk": 1, "slot_id": 1, "port_id": 2, "onu_id": 3, "onu_serial": "ZTEG1"}


OLTS = [{"olt_pk": 1, "olt_name": "OLT_C610-01", "olt_vendor": 4, "olt_enabled": True},
        {"olt_pk": 2, "olt_name": "OLT_SAPE", "olt_vendor": 6, "olt_enabled": True}]
TEMPLATES = [{"tpl_pk": 1002, "tpl_name": "ZTE-C610-01", "tpl_vendor": 4, "tpl_type": 0, "tpl_enabled": True},
             {"tpl_pk": 1007, "tpl_name": "ZTE-C610-02", "tpl_vendor": 4, "tpl_type": 0, "tpl_enabled": True}]


def _cpes(monkeypatch, linhas):
    from app.brbyteapi.controllr import AsyncControllr

    aplicados = []

    async def falso_combo(self, body, model_return=False):
        return SimpleNamespace(success=True, results=linhas)

    # Consultas de OLT/template respondem; só o que grava vai para "aplicados".
    async def falso_post(self, caminho, body):
        if caminho == "/fiber_ctl/olt/list":
            return SimpleNamespace(success=True, results=OLTS, errors=None)
        if caminho == "/fiber_ctl/tpl/list":
            return SimpleNamespace(success=True, results=TEMPLATES, errors=None)
        aplicados.append(body)
        return SimpleNamespace(success=True, results=[], errors=None)

    monkeypatch.setattr(AsyncControllr, "cpe_list_combo", falso_combo)
    monkeypatch.setattr(AsyncControllr, "call_api_post", falso_post)
    return aplicados


def test_associar_usa_a_conexao_pedida_mesmo_fora_de_ordem(cliente, monkeypatch):
    # Controllr ignorou o filtro e devolveu outras conexões antes da certa.
    aplicados = _cpes(monkeypatch, [
        {"cpe_pk": 99, "client_pk": 55, "cpe_username": "outro.cliente", "cpe_password": "x"},
        {"cpe_pk": 20, "client_pk": 10, "cpe_username": "maria.silva", "cpe_password": "y"},
    ])
    assert cliente.post("/onu/77/associar-cliente", json=PAYLOAD).status_code == 200
    assert "maria.silva" in aplicados[0] and "outro.cliente" not in aplicados[0]


MARIA = [{"cpe_pk": 20, "client_pk": 10, "cpe_username": "maria.silva", "cpe_password": "y"}]


def test_associar_onu_sem_template_continua_sem(cliente, monkeypatch):
    aplicados = _cpes(monkeypatch, MARIA)
    assert cliente.post("/onu/0/associar-cliente", json={**PAYLOAD, "wan_tpl_pk": 0}).status_code == 200
    assert "wan_tpl_pk=0" in aplicados[0]


def test_associar_mantem_template_existente_e_vsol_sem_template(cliente, monkeypatch):
    aplicados = _cpes(monkeypatch, MARIA)
    cliente.post("/onu/0/associar-cliente", json={**PAYLOAD, "wan_tpl_pk": 1007})
    cliente.post("/onu/0/associar-cliente", json={**PAYLOAD, "olt_pk": 2, "wan_tpl_pk": 0})
    assert "wan_tpl_pk=1007" in aplicados[0]
    assert "wan_tpl_pk=0" in aplicados[1]


def test_associar_com_template_escolhido(cliente, monkeypatch):
    aplicados = _cpes(monkeypatch, MARIA)
    assert cliente.post("/onu/0/associar-cliente", json={**PAYLOAD, "wan_tpl_pk": 1007}).status_code == 200
    assert "wan_tpl_pk=1007" in aplicados[0]
    # Template de outro fabricante ou inexistente: recusa sem gravar.
    assert cliente.post("/onu/0/associar-cliente", json={**PAYLOAD, "wan_tpl_pk": 1009}).status_code == 400
    assert len(aplicados) == 1


def test_templates_da_olt(cliente, monkeypatch):
    _cpes(monkeypatch, MARIA)
    corpo = cliente.get("/onu/templates", params={"olt_pk": 1}).json()
    assert corpo["padrao"] == 1002
    assert [t["pk"] for t in corpo["results"]] == [1002, 1007]
    assert cliente.get("/onu/templates", params={"olt_pk": 2}).json()["results"] == []


ONU_LIDA = {"olt_pk": 1, "olt_name": "OLT_C610-01", "frame_id": 1, "slot_id": 3, "port_id": 1, "onu_id": 1,
            "onu_serial": "42061D738D98", "onu_pk": 0, "client_pk": 0, "contract_pk": 0, "cpe_pk": 0,
            "wan_tpl_pk": 0, "onu_wancfg_conntype": 0, "onu_wancfg_gemport": 0, "onu_wancfg_vlanid": 0,
            "onu_wancfg_pppoe_username": "", "onu_wancfg_pppoe_passwd": ""}


def _template(monkeypatch, onus):
    from app.brbyteapi.controllr import AsyncControllr

    aplicados = []

    async def falso_onu_list(self, body, model_return=False, model_extended=False):
        return SimpleNamespace(success=True, results=onus)

    async def falso_post(self, caminho, body):
        if caminho == "/fiber_ctl/olt/list":
            return SimpleNamespace(success=True, results=OLTS, errors=None)
        if caminho == "/fiber_ctl/tpl/list":
            return SimpleNamespace(success=True, results=TEMPLATES, errors=None)
        aplicados.append((caminho, body))
        return SimpleNamespace(success=True, results=[], errors=None)

    monkeypatch.setattr(AsyncControllr, "onu_list", falso_onu_list)
    monkeypatch.setattr(AsyncControllr, "call_api_post", falso_post)
    return aplicados


def test_template_na_onu_sem_cliente(cliente, monkeypatch, tmp_path):
    from app import audit

    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", str(tmp_path / "auditoria.jsonl"))
    monkeypatch.setattr(audit, "_ultimo_mes_limpo", None)
    aplicados = _template(monkeypatch, [ONU_LIDA])
    corpo = {"olt_pk": 1, "onu_serial": "42061d738d98", "wan_tpl_pk": 1007, "template_anterior": "ZTE-C610-01"}
    assert cliente.post("/onu/template", json=corpo).status_code == 200
    caminho, body = aplicados[0]
    assert caminho == "/fiber_ctl/onu/apply_wan"
    for parte in ("wan_tpl_pk=1007", "onu_wancfg_conntype=1", "onu_wancfg_gemport=1", "client_pk=0",
                  "onu_wancfg_pppoe_username=&", "slot_id=3", "onu_id=1"):
        assert parte in body
    arquivo = next(tmp_path.glob("auditoria-*.jsonl"))
    ev = json.loads(arquivo.read_text(encoding="utf-8").splitlines()[-1])
    assert ev["acao"] == "onu.template"
    assert ev["alvo"]["template"] == "ZTE-C610-02" and ev["alvo"]["template_anterior"] == "ZTE-C610-01"


def test_template_mantem_cliente_e_pppoe_da_onu(cliente, monkeypatch):
    com_cliente = {**ONU_LIDA, "client_pk": 10, "cpe_pk": 20, "contract_pk": 30, "wan_tpl_pk": 1002,
                   "onu_wancfg_conntype": 1, "onu_wancfg_gemport": 1, "onu_wancfg_vlanid": 100,
                   "onu_wancfg_pppoe_username": "maria.silva", "onu_wancfg_pppoe_passwd": "y"}
    aplicados = _template(monkeypatch, [com_cliente])
    cliente.post("/onu/template", json={"olt_pk": 1, "onu_serial": "42061D738D98", "wan_tpl_pk": 1007})
    body = aplicados[0][1]
    assert "client_pk=10" in body and "cpe_pk=20" in body and "maria.silva" in body and "onu_wancfg_vlanid=100" in body


@pytest.mark.parametrize("corpo, onus", [
    ({"olt_pk": 1, "onu_serial": "42061D738D98", "wan_tpl_pk": 1009}, [ONU_LIDA]),   # outro fabricante
    ({"olt_pk": 2, "onu_serial": "42061D738D98", "wan_tpl_pk": 1009}, [ONU_LIDA]),   # VSOL
    ({"olt_pk": 1, "onu_serial": "42061D738D98", "wan_tpl_pk": 1007}, []),           # ONU não achada
    ({"olt_pk": 1, "onu_serial": "42061D738D98", "wan_tpl_pk": 1007}, [{**ONU_LIDA, "olt_pk": 3}]),  # outra OLT
])
def test_template_recusa(cliente, monkeypatch, corpo, onus):
    aplicados = _template(monkeypatch, onus)
    assert cliente.post("/onu/template", json=corpo).status_code in (400, 404)
    assert aplicados == []


def _provisionar(monkeypatch, pendentes):
    from app.brbyteapi.controllr import AsyncControllr

    gravados = []

    async def falso_post(self, caminho, body):
        if caminho == "/fiber_ctl/olt/list":
            return SimpleNamespace(success=True, results=OLTS, errors=None)
        if caminho == "/fiber_ctl/onu/list_unreg":
            return SimpleNamespace(success=True, results=pendentes if "olt_pk=1&" in body else [], errors=None)
        gravados.append((caminho, body))
        return SimpleNamespace(success=True, results=[], errors=None)

    monkeypatch.setattr(AsyncControllr, "call_api_post", falso_post)
    return gravados


PENDENTE = [{"onu_serial": "ZTEG12345678", "slot_id": 3, "port_id": 5, "onu_model": "ZTE-F601"}]
PROVISIONAR = {"olt_pk": 1, "slot_id": 3, "port_id": 5, "onu_serial": "zteg12345678", "onu_name": "ONU_ZTEG12345678"}


def test_lista_nao_registradas_so_das_zte(cliente, monkeypatch):
    _provisionar(monkeypatch, PENDENTE)
    corpo = cliente.get("/onu/nao-registradas").json()
    assert corpo["olts"] == ["OLT_C610-01"]
    assert [o["serial"] for o in corpo["results"]] == ["ZTEG12345678"]


def test_provisionar_registra_com_modelo_da_olt(cliente, monkeypatch, tmp_path):
    from app import audit

    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", str(tmp_path / "auditoria.jsonl"))
    gravados = _provisionar(monkeypatch, PENDENTE)
    assert cliente.post("/onu/provisionar", json=PROVISIONAR).status_code == 200
    caminho, body = gravados[0]
    assert caminho == "/fiber_ctl/onu/add"
    assert "onu_serial=ZTEG12345678" in body and "onu_model=ZTE-F601" in body and "onu_name=ONU_ZTEG12345678" in body


def test_provisionar_recusa_onu_que_nao_esta_pendente(cliente, monkeypatch):
    gravados = _provisionar(monkeypatch, [])
    assert cliente.post("/onu/provisionar", json=PROVISIONAR).status_code == 409
    assert cliente.post("/onu/provisionar", json={**PROVISIONAR, "olt_pk": 2}).status_code == 400
    assert gravados == []


@pytest.mark.parametrize("linhas", [
    [{"cpe_pk": 99, "client_pk": 10, "cpe_username": "outra.conexao"}],   # conexão errada
    [{"cpe_pk": 20, "client_pk": 55, "cpe_username": "outro.cliente"}],   # conexão de outro cliente
    [],
])
def test_associar_recusa_quando_nao_confere(cliente, monkeypatch, linhas):
    aplicados = _cpes(monkeypatch, linhas)
    assert cliente.post("/onu/77/associar-cliente", json=PAYLOAD).status_code == 404
    assert aplicados == []


def test_associar_grava_auditoria_com_antes_e_depois(cliente, monkeypatch, tmp_path):
    from app import audit

    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", str(tmp_path / "auditoria.jsonl"))
    monkeypatch.setattr(audit, "_ultimo_mes_limpo", None)
    aplicados = _cpes(monkeypatch, [
        {"cpe_pk": 20, "client_pk": 10, "client_complete_name": "Maria da Silva",
         "cpe_username": "maria.silva", "cpe_password": "segredo"},
    ])
    contexto = {"olt_nome": "OLT-CENTRO", "cliente_anterior": "José Souza", "pppoe_anterior": "jose.souza"}
    assert cliente.post("/onu/77/associar-cliente", json={**PAYLOAD, "auditoria": contexto}).status_code == 200

    assert "OLT-CENTRO" not in aplicados[0]  # o contexto não vai para o Controllr
    (ev,), _ = audit.consultar()
    assert ev["acao"] == "onu.associar_cliente" and ev["tecnico"] == "joao"
    assert ev["alvo"] == {"onu_pk": 77, "olt_pk": 1, "olt_nome": "OLT-CENTRO", "porta": 2, "onu_id": 3,
                          "serial": "ZTEG1", "cpe_pk": 20, "cliente": "Maria da Silva", "pppoe": "maria.silva",
                          "cliente_anterior": "José Souza", "pppoe_anterior": "jose.souza"}
    assert "segredo" not in str(ev)


def test_associacao_recusada_nao_grava_auditoria(cliente, monkeypatch, tmp_path):
    from app import audit

    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", str(tmp_path / "auditoria.jsonl"))
    _cpes(monkeypatch, [{"cpe_pk": 20, "client_pk": 55}])
    assert cliente.post("/onu/77/associar-cliente", json=PAYLOAD).status_code == 404
    assert audit.consultar() == ([], False)


def test_erro_do_controllr_no_anexo_nao_vira_401(cliente, monkeypatch):
    # Um 401 repassado faria o app achar que a sessão do técnico caiu.
    from app.routers import suporte

    class _Sessao:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, headers=None):
            return _RespostaFalsa("text/html", status=401)

    monkeypatch.setattr(suporte.aiohttp, "ClientSession", _Sessao)
    assert cliente.get("/suporte/tickets/1/anexos/foto.jpg").status_code == 502


def test_anexo_grande_demais_e_recusado(cliente, monkeypatch):
    from app.deps import AuthContext, get_auth_context
    from app.main import app
    from app.routers import suporte

    enviados = []

    class _Controllr:
        async def support_annex_upload(self, form):
            enviados.append(form)

    app.dependency_overrides[get_auth_context] = lambda: AuthContext(session=sessao(), controllr=_Controllr())
    monkeypatch.setattr(suporte, "ANEXO_MAX_MB", 1)
    r = cliente.post("/suporte/tickets/1/anexos", files={"file": ("video.mp4", b"0" * (1024 * 1024 + 1), "video/mp4")})
    assert r.status_code == 413
    assert enviados == []
