"""Provisionamento de ONU: template de WAN da OLT e linha de list_unreg."""

from app.routers.onu import onu_nao_registrada, template_wan_padrao

# Templates de WAN reais da SUPERSONIC (23/11), só os campos usados.
TEMPLATES = [
    {"tpl_pk": 11, "tpl_name": "FIBERHOME WAN BRIDGE", "tpl_vendor": 1, "tpl_type": 0, "tpl_enabled": True},
    {"tpl_pk": 1009, "tpl_name": "VSOL1600G", "tpl_vendor": 6, "tpl_type": 0, "tpl_enabled": True},
    {"tpl_pk": 1007, "tpl_name": "ZTE-C610-02", "tpl_vendor": 4, "tpl_type": 0, "tpl_enabled": True},
    {"tpl_pk": 1002, "tpl_name": "ZTE-C610-01", "tpl_vendor": 4, "tpl_type": 0, "tpl_enabled": True},
    {"tpl_pk": 1003, "tpl_name": "ZTE REBOOT", "tpl_vendor": 4, "tpl_type": 3, "tpl_enabled": True},
]


def test_template_pelo_nome_da_olt_zte():
    assert template_wan_padrao({"olt_vendor": 4, "olt_name": "OLT_C610-01"}, TEMPLATES) == 1002
    assert template_wan_padrao({"olt_vendor": 4, "olt_name": "OLT_C610-02"}, TEMPLATES) == 1007


def test_vsol_nao_recebe_template():
    assert template_wan_padrao({"olt_vendor": 6, "olt_name": "OLT_SAPE"}, TEMPLATES) is None


def test_zte_sem_template_claro_fica_sem():
    assert template_wan_padrao({"olt_vendor": 4, "olt_name": "OLT_NOVA"}, TEMPLATES) is None
    inativo = [{**t, "tpl_enabled": t["tpl_pk"] != 1007} for t in TEMPLATES]
    # Sobrou um só ativo do fabricante: é ele.
    assert template_wan_padrao({"olt_vendor": 4, "olt_name": "OLT_NOVA"}, inativo) == 1002


def test_linha_nao_registrada():
    olt = {"olt_pk": 1, "olt_name": "OLT_C610-01"}
    linha = {"onu_serial": " zteg12345678 ", "slot_id": "3", "port_id": 5, "onu_model": "ZTE-F601"}
    assert onu_nao_registrada(linha, olt) == {
        "olt_pk": 1, "olt_nome": "OLT_C610-01", "frame_id": 1, "slot_id": 3, "port_id": 5,
        "serial": "ZTEG12345678", "modelo": "ZTE-F601",
    }


def test_linha_incompleta_e_ignorada():
    assert onu_nao_registrada({"onu_serial": "X", "slot_id": 3}, {"olt_pk": 1}) is None
    assert onu_nao_registrada({"onu_serial": "X", "slot_id": "a", "port_id": 1}, {"olt_pk": 1}) is None
