import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..audit import registrar_auditoria
from ..deps import AuthContext, get_auth_context
from ..http_errors import detalhe_erro
from ..where import corpo, where_eq

router = APIRouter(prefix="/onu", tags=["onu"])
logger = logging.getLogger(__name__)

# Fabricantes de OLT em que a ONU nova precisa ser registrada pelo técnico
# (/fiber_ctl/onu/add) e recebe o template de WAN da OLT. ZTE = 4. As VSOL
# (6) registram a ONU sozinhas e não usam template (wan_tpl_pk 0 em todas).
FABRICANTES_PROVISIONAMENTO = {4}

# A OLT precisa desse intervalo para recarregar antes que reler a ONU
# traga dado novo — reler imediatamente após o reconnect devolveria o
# valor antigo. Ajustar `deploy/nginx.conf.example`/
# `apache-vhost.conf.example` (proxy_read_timeout/ProxyTimeout) se esses
# valores mudarem, senão o reverse proxy corta a requisição antes do
# backend responder.
ESPERA_APOS_RECONECTAR_OLT_S = 15
ESPERA_APOS_ATUALIZAR_ONU_S = 30


def _corpo_busca_wizard(search_term: str, search_value: str, sort: str) -> str:
    """
    Formato "wizard" de busca (search_term/search_value), mais confiável
    que filtrar /fiber_ctl/onu/list por "cpe_pk" no "where": esse campo é
    ambíguo (o endpoint também traz client_pk/contract_number via join) e
    o Controllr ignora o filtro quebrado, devolvendo a ONU de qualquer
    cliente em vez de vazio ou erro.
    """
    campos = {
        "olt_pk": 0,
        "dp_pk": -1,
        "search_term": search_term,
        "search_value": search_value,
        "frame_id": -1,
        "slot_id": -1,
        "port_id": -1,
        "onu_id": -1,
        "duplicate_serial": -1,
        "signal_min_limit": 128,
        "signal_max_limit": 128,
        "page": 1,
        "start": 0,
        "limit": 15,
        "sort": sort,
        "dir": "ASC",
    }
    return urlencode(campos)


def pppoe_igual(onu: Any, username: str) -> bool:
    """A busca do Controllr por PPPoE é por trecho: "silva@x" também
    traz "mariasilva@x" (visto ao vivo em 06/11, ONU de outro cliente
    no "Pronto para fechar"). Só vale o PPPoE exato, sem diferenciar
    maiúsculas — da ONU ou, na falta dele, da conexão ligada a ela."""
    alvo = username.strip().lower()
    for campo in ("wancfg_pppoe_username", "cpe_username"):
        valor = getattr(onu, campo, None)
        if isinstance(valor, str) and valor.strip():
            return valor.strip().lower() == alvo
    return False


@router.get("/busca")
async def buscar_onu(
    serial: str | None = Query(default=None, description="Serial da ONU, impresso no equipamento"),
    username: str | None = Query(default=None, description="Usuário PPPoE do CPE do cliente — filtro confiável para localizar a ONU dele"),
    cpe_pk: int | None = Query(default=None),
    olt_pk: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    if not serial and not username and not cpe_pk and not olt_pk:
        raise HTTPException(status_code=400, detail="Informe serial, username, cpe_pk ou olt_pk.")

    if serial:
        corpo_requisicao = _corpo_busca_wizard("onu_serial", serial.strip().upper(), sort="onu_ponid")
    elif username:
        corpo_requisicao = _corpo_busca_wizard("onu_wancfg_pppoe_username", username.strip(), sort="onu_pk")
    else:
        # "cpe_pk" puro é ambíguo aqui (ver docstring acima) — opção de
        # baixo nível; "username" é o filtro preferido para achar a ONU de
        # um cliente específico. O filtro abaixo garante não devolver a
        # ONU de outro cpe_pk mesmo que o "where" seja ignorado.
        campo, valor = ("olt_pk", olt_pk) if not cpe_pk else ("cpe_pk", cpe_pk)
        corpo_requisicao = corpo(where_eq(campo, valor), limit=20)

    resposta = await ctx.controllr.onu_list(corpo_requisicao, model_return=True, model_extended=True)
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível consultar a ONU.", resposta))

    resultados = resposta.results
    if cpe_pk is not None:
        resultados = [onu for onu in resultados if onu.cpe_pk == cpe_pk]
    if username and not serial:
        resultados = [onu for onu in resultados if pppoe_igual(onu, username)]

    return {"success": True, "results": [onu.model_dump(mode="json") for onu in resultados]}


# ------------------------------------------------------------ provisionar


def _primeiro(linha: dict[str, Any], *chaves: str) -> Any:
    for chave in chaves:
        valor = linha.get(chave)
        if valor not in (None, ""):
            return valor
    return None


def onu_nao_registrada(linha: dict[str, Any], olt: dict[str, Any]) -> dict[str, Any] | None:
    """Linha de /fiber_ctl/onu/list_unreg no formato da tela. Os nomes dos
    campos seguem os de /fiber_ctl/onu/add e de /fiber_ctl/onu/list, com
    alternativas: até 23/11 nenhuma ONU estava pendente para conferir ao vivo."""
    serial = _primeiro(linha, "onu_serial", "serial", "onu_sn", "sn")
    slot = _primeiro(linha, "slot_id", "onu_slot_id", "slot")
    porta = _primeiro(linha, "port_id", "onu_port_id", "pon_id", "port")
    if serial is None or slot is None or porta is None:
        return None
    try:
        slot, porta = int(slot), int(porta)
        frame = int(_primeiro(linha, "frame_id", "onu_frame_id") or 1)
    except (TypeError, ValueError):
        return None
    return {
        "olt_pk": olt.get("olt_pk"),
        "olt_nome": olt.get("olt_name"),
        "frame_id": frame,
        "slot_id": slot,
        "port_id": porta,
        "serial": str(serial).strip().upper(),
        "modelo": str(_primeiro(linha, "onu_model", "model", "onu_type", "type") or ""),
    }


def template_wan_padrao(olt: dict[str, Any], templates: list[dict[str, Any]]) -> int | None:
    """Template de WAN que a ONU nova recebe: o do mesmo fabricante da OLT,
    ativo e de WAN (tpl_type 0). Com mais de um (ZTE-C610-01 e ZTE-C610-02),
    o que tem o nome da OLT sem o prefixo "OLT_". Sem um claro, nenhum."""
    if olt.get("olt_vendor") not in FABRICANTES_PROVISIONAMENTO:
        return None
    candidatos = [
        t for t in templates
        if t.get("tpl_type") == 0 and t.get("tpl_enabled") and t.get("tpl_vendor") == olt.get("olt_vendor")
    ]
    if len(candidatos) == 1:
        return int(candidatos[0]["tpl_pk"])
    nome_olt = re.sub(r"^OLT[_\-\s]*", "", str(olt.get("olt_name") or ""), flags=re.IGNORECASE).upper()
    pelo_nome = [t for t in candidatos if nome_olt and nome_olt in str(t.get("tpl_name") or "").upper()]
    return int(pelo_nome[0]["tpl_pk"]) if len(pelo_nome) == 1 else None


async def _olts(ctx: AuthContext) -> list[dict[str, Any]]:
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/olt/list", urlencode({"page": 1, "start": 0, "limit": 100}))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível consultar as OLTs.", resposta))
    return [o for o in resposta.results if o.get("olt_enabled") is not False]


async def _nao_registradas_da_olt(ctx: AuthContext, olt: dict[str, Any]) -> list[dict[str, Any]]:
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/onu/list_unreg", urlencode(
        {"olt_pk": olt["olt_pk"], "frame_id": -1, "slot_id": -1, "port_id": -1, "page": 1, "start": 0, "limit": 100}))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro(
            f"Não foi possível consultar as ONUs aguardando registro na {olt.get('olt_name')}.", resposta))
    if resposta.results:
        # Para conferir os nomes dos campos na primeira ONU real (ver onu_nao_registrada).
        logger.info("list_unreg %s: campos %s", olt.get("olt_name"), sorted(resposta.results[0]))
    return [onu for linha in resposta.results if (onu := onu_nao_registrada(linha, olt))]


@router.get("/nao-registradas")
async def listar_nao_registradas(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    """ONUs ligadas na fibra e ainda não registradas, nas OLTs em que o
    registro é feito pelo técnico (FABRICANTES_PROVISIONAMENTO)."""
    olts = [o for o in await _olts(ctx) if o.get("olt_vendor") in FABRICANTES_PROVISIONAMENTO]
    listas = await asyncio.gather(*(_nao_registradas_da_olt(ctx, olt) for olt in olts))
    return {"success": True, "results": [onu for lista in listas for onu in lista],
            "olts": [o.get("olt_name") for o in olts]}


class ProvisionarPayload(BaseModel):
    olt_pk: int
    slot_id: int
    port_id: int
    onu_serial: str = Field(min_length=4, max_length=64)
    onu_name: str = Field(min_length=1, max_length=64)


@router.post("/provisionar")
async def provisionar_onu(
    payload: ProvisionarPayload, request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    # Confere no Controllr que a ONU ainda está aguardando registro naquela
    # OLT/PON (a tela pode estar velha, ou outro técnico já registrou), e
    # usa o modelo informado pela própria OLT, não o que veio da tela.
    olt = next((o for o in await _olts(ctx) if o.get("olt_pk") == payload.olt_pk), None)
    if olt is None or olt.get("olt_vendor") not in FABRICANTES_PROVISIONAMENTO:
        raise HTTPException(status_code=400, detail="Esta OLT registra as ONUs sozinha.")
    serial = payload.onu_serial.strip().upper()
    pendente = next(
        (o for o in await _nao_registradas_da_olt(ctx, olt)
         if o["serial"] == serial and o["slot_id"] == payload.slot_id and o["port_id"] == payload.port_id),
        None,
    )
    if pendente is None:
        raise HTTPException(status_code=409, detail="Esta ONU não está mais aguardando registro. Atualize a lista.")

    nome = payload.onu_name.strip()
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/onu/add", urlencode({
        "olt_pk": payload.olt_pk,
        "onu_name": nome,
        "line_id": 0,
        "srv_id": 0,
        "onu_model": pendente["modelo"],
        "slot_id": payload.slot_id,
        "port_id": payload.port_id,
        "onu_serial": serial,
    }))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível registrar a ONU.", resposta))
    # Ainda não tem onu_pk nem número na PON: a OLT atribui ao registrar
    # (aparece em /fiber_ctl/onu/list em cerca de 1 minuto).
    registrar_auditoria(request, ctx.session, "onu.provisionar", {
        "olt_pk": payload.olt_pk, "olt_nome": olt.get("olt_name"), "porta": payload.port_id,
        "slot": payload.slot_id, "serial": serial, "onu_name": nome, "modelo": pendente["modelo"] or None,
    })
    return {"success": True, "results": resposta.results}


def templates_wan_da_olt(olt: dict[str, Any], templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Templates de WAN que o técnico pode escolher para a ONU desta OLT: os
    ativos do mesmo fabricante. Um template novo criado no Controllr (ex.:
    perfil de cliente específico) aparece aqui sem mudar o app."""
    if olt.get("olt_vendor") not in FABRICANTES_PROVISIONAMENTO:
        return []
    return [
        {"pk": int(t["tpl_pk"]), "nome": str(t.get("tpl_name") or t["tpl_pk"])}
        for t in templates
        if t.get("tpl_type") == 0 and t.get("tpl_enabled") and t.get("tpl_vendor") == olt.get("olt_vendor")
    ]


async def _templates_da_olt(ctx: AuthContext, olt_pk: int) -> tuple[list[dict[str, Any]], int | None]:
    """(templates que podem ser escolhidos, o padrão da OLT). Vazio nas VSOL."""
    olt = next((o for o in await _olts(ctx) if o.get("olt_pk") == olt_pk), None)
    if olt is None or olt.get("olt_vendor") not in FABRICANTES_PROVISIONAMENTO:
        return [], None
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/tpl/list", urlencode({"page": 1, "start": 0, "limit": 200}))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível consultar os templates.", resposta))
    return templates_wan_da_olt(olt, resposta.results), template_wan_padrao(olt, resposta.results)


@router.get("/templates")
async def listar_templates_wan(
    olt_pk: int = Query(...), ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    templates, padrao = await _templates_da_olt(ctx, olt_pk)
    return {"success": True, "results": templates, "padrao": padrao}


@router.post("/{onu_pk}/atualizar")
async def atualizar_info_onu(
    onu_pk: int,
    request: Request,
    olt_pk: int = Query(...),
    onu_serial: str = Query(...),
    slot_id: int = Query(...),
    port_id: int = Query(...),
    onu_id: int = Query(...),
    frame_id: int = Query(default=1),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    # Reconecta a OLT antes de atualizar: força o sistema a reler a OLT
    # (não derruba as ONUs conectadas) e garante que a atualização abaixo
    # traga o dado mais recente, não um valor em cache. Fluxo: reconectar
    # → aguardar a OLT recarregar → atualizar a ONU → aguardar refletir →
    # devolver os dados novos para o chamador reler.
    await ctx.controllr.call_api_post("/fiber_ctl/olt/reconnect", urlencode({"olt_pk": olt_pk}))
    await asyncio.sleep(ESPERA_APOS_RECONECTAR_OLT_S)

    resposta = await ctx.controllr.onu_update_info(
        olt_pk=olt_pk, onu_serial=onu_serial, slot_id=slot_id, port_id=port_id, onu_id=onu_id, frame_id=frame_id
    )
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível atualizar a ONU.", resposta))
    # O nome da OLT a tela de auditoria completa pelo onu_pk (routers/auditoria.py).
    registrar_auditoria(request, ctx.session, "onu.atualizar",
                        _alvo_onu(onu_pk, olt_pk, port_id, onu_id, None, onu_serial))

    await asyncio.sleep(ESPERA_APOS_ATUALIZAR_ONU_S)
    return {"success": True, "results": resposta.results}


def _alvo_onu(onu_pk: int, olt_pk: int, porta: int, onu_id: int, olt_nome: str | None,
              onu_serial: str | None, **extra: Any) -> dict[str, Any]:
    """O que vai para a auditoria: além dos pks do Controllr, o que a pessoa
    reconhece na tela — nome da OLT, PON e número da ONU nela, serial (o nome
    e o serial vêm da tela, como estavam no momento da ação)."""
    alvo = {"onu_pk": onu_pk, "olt_pk": olt_pk, "olt_nome": olt_nome, "porta": porta, "onu_id": onu_id,
            "serial": onu_serial, **extra}
    return {k: v for k, v in alvo.items() if v not in (None, "")}


# Endpoints não documentados na doc oficial. Corpo dos dois:
# olt_pk/frame_id/slot_id/port_id/onu_id — mesmos identificadores OSPO já
# usados por onu_update_info acima, sem o serial.
@router.post("/{onu_pk}/reiniciar")
async def reiniciar_onu(
    onu_pk: int,
    request: Request,
    olt_pk: int = Query(...),
    slot_id: int = Query(...),
    port_id: int = Query(...),
    onu_id: int = Query(...),
    frame_id: int = Query(default=1),
    olt_nome: str | None = Query(default=None, max_length=128),
    onu_serial: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    resposta = await ctx.controllr.call_api_post(
        "/fiber_ctl/onu/apply_reboot",
        urlencode({"olt_pk": olt_pk, "frame_id": frame_id, "slot_id": slot_id, "port_id": port_id, "onu_id": onu_id}),
    )
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível reiniciar a ONU.", resposta))
    registrar_auditoria(request, ctx.session, "onu.reiniciar",
                        _alvo_onu(onu_pk, olt_pk, port_id, onu_id, olt_nome, onu_serial))
    return {"success": True, "results": resposta.results}


@router.post("/{onu_pk}/remover")
async def remover_onu(
    onu_pk: int,
    request: Request,
    olt_pk: int = Query(...),
    slot_id: int = Query(...),
    port_id: int = Query(...),
    onu_id: int = Query(...),
    frame_id: int = Query(default=1),
    olt_nome: str | None = Query(default=None, max_length=128),
    onu_serial: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    resposta = await ctx.controllr.call_api_post(
        "/fiber_ctl/onu/delete",
        urlencode({"olt_pk": olt_pk, "frame_id": frame_id, "slot_id": slot_id, "port_id": port_id, "onu_id": onu_id}),
    )
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível remover a ONU.", resposta))
    registrar_auditoria(request, ctx.session, "onu.remover",
                        _alvo_onu(onu_pk, olt_pk, port_id, onu_id, olt_nome, onu_serial))
    return {"success": True, "results": resposta.results}


@router.post("/{onu_pk}/renomear")
async def renomear_onu(
    onu_pk: int,
    request: Request,
    onu_name: str = Query(..., min_length=1, max_length=64),
    olt_pk: int = Query(...),
    slot_id: int = Query(...),
    port_id: int = Query(...),
    onu_id: int = Query(...),
    frame_id: int = Query(default=1),
    olt_nome: str | None = Query(default=None, max_length=128),
    onu_serial: str | None = Query(default=None, max_length=64),
    nome_anterior: str | None = Query(default=None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    # Mesmos identificadores OSPO dos dois endpoints acima + "onu_name"
    # (rótulo do campo no painel é "Descrição", mas o form submete
    # "onu_name" — não é o mesmo campo de "onu_desc" do modelo).
    resposta = await ctx.controllr.call_api_post(
        "/fiber_ctl/onu/apply_rename",
        urlencode({"olt_pk": olt_pk, "frame_id": frame_id, "slot_id": slot_id, "port_id": port_id, "onu_id": onu_id, "onu_name": onu_name}),
    )
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível renomear a ONU.", resposta))
    registrar_auditoria(request, ctx.session, "onu.renomear",
                        _alvo_onu(onu_pk, olt_pk, port_id, onu_id, olt_nome, onu_serial,
                                  onu_name=onu_name, nome_anterior=nome_anterior))
    return {"success": True, "results": resposta.results}


def _mesmo_pk(valor: Any, esperado: int) -> bool:
    try:
        return int(valor) == esperado
    except (TypeError, ValueError):
        return False


class ContextoAuditoriaAssociacao(BaseModel):
    """Como a ONU estava na tela antes de associar — só vai para a auditoria."""

    olt_nome: str | None = Field(default=None, max_length=128)
    cliente_anterior: str | None = Field(default=None, max_length=128)
    pppoe_anterior: str | None = Field(default=None, max_length=64)


class AssociarClientePayload(BaseModel):
    client_pk: int
    # O cliente pode ter mais de uma conexão (CPE) cadastrada — o técnico
    # escolhe explicitamente qual usuário PPPoE vincular a esta ONU, em
    # vez de presumir a primeira automaticamente.
    cpe_pk: int
    olt_pk: int
    slot_id: int
    port_id: int
    onu_id: int
    onu_serial: str
    frame_id: int = 1
    # Config de WAN da própria ONU (Vlan, Cos, Template etc.) — o técnico
    # não edita nada disso aqui, só reenvia o que a tela já tinha carregado
    # (ver OnuDto em api/client.ts). /fiber_ctl/onu/apply_wan exige o
    # registro completo (mesmo padrão de phone/update); sem isso, salvar
    # zeraria a config de rede da própria ONU.
    wancfg_conntype: int | None = None
    wan_tpl_pk: int | None = None
    wancfg_vlanid: int | None = None
    wancfg_user_vlanid: int | None = None
    wancfg_cos: int | None = None
    wancfg_tcont: int | None = None
    wancfg_gemport: int | None = None
    wancfg_svlan: int | None = None
    wancfg_stpid: int | None = None
    wancfg_scos: int | None = None
    wancfg_pon_profile: str | None = None
    wancfg_pppoe_svcname: str | None = None
    wancfg_local_ip: str | None = None
    auditoria: ContextoAuditoriaAssociacao | None = None


async def _aplicar_wan(
    ctx: AuthContext, payload: AssociarClientePayload, erro: str
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    """apply_wan com o registro completo: cliente/PPPoE da CPE conferida e
    o template de WAN da OLT. Devolve (resposta, cpe, templates da OLT)."""
    # Reproduz a tela "Informações" da ONU do painel (sem documentação
    # oficial): busca as CPEs do cliente via /aaa_ctl/cpe/list_combo e usa
    # usuário/senha PPPoE da CPE escolhida — não existe cadastro de PPPoE
    # "solto", é sempre o acesso que já existe no cadastro do cliente.
    # Confere a linha devolvida: o Controllr ignora filtro que não entende e
    # devolve qualquer CPE — pegar a primeira às cegas gravaria na ONU o
    # usuário/senha PPPoE de outra conexão, até de outro cliente.
    cpes_resp = await ctx.controllr.cpe_list_combo(corpo(where_eq("aaa_cpe.cpe_pk", payload.cpe_pk), limit=20))
    cpes = cpes_resp.results if cpes_resp.success else []
    cpe = next((c for c in cpes if _mesmo_pk(c.get("cpe_pk"), payload.cpe_pk)), None)
    if cpe is None or not _mesmo_pk(cpe.get("client_pk"), payload.client_pk):
        raise HTTPException(status_code=404, detail="Conexão (CPE) não encontrada para este cliente.")

    # OLT ZTE: o técnico escolhe o template de WAN (perfil do cliente); só
    # vale um ativo do fabricante da OLT. ONU recém-registrada vem sem
    # template: sem escolha, usa o da OLT. Com template, PPPoE (conntype 1)
    # e gemport 1, como as demais ONUs dessas OLTs. Nas VSOL nada muda.
    templates, padrao = await _templates_da_olt(ctx, payload.olt_pk)
    if templates:
        if payload.wan_tpl_pk and payload.wan_tpl_pk not in {t["pk"] for t in templates}:
            raise HTTPException(status_code=400, detail="Template de WAN não disponível para esta OLT.")
        payload.wan_tpl_pk = payload.wan_tpl_pk or padrao
        if payload.wan_tpl_pk:
            payload.wancfg_conntype = payload.wancfg_conntype or 1
            payload.wancfg_gemport = payload.wancfg_gemport or 1

    campos = {
        "olt_pk": payload.olt_pk,
        "frame_id": payload.frame_id,
        "slot_id": payload.slot_id,
        "port_id": payload.port_id,
        "onu_id": payload.onu_id,
        "onu_serial": payload.onu_serial,
        "client_pk": payload.client_pk,
        "contract_pk": cpe.get("contract_pk"),
        "cpe_pk": cpe.get("cpe_pk"),
        "onu_wancfg_pppoe_username": cpe.get("cpe_username"),
        "onu_wancfg_pppoe_passwd": cpe.get("cpe_password"),
        "onu_wancfg_pppoe_svcname": payload.wancfg_pppoe_svcname or "",
        "onu_wancfg_conntype": payload.wancfg_conntype,
        "wan_tpl_pk": payload.wan_tpl_pk,
        "onu_wancfg_vlanid": payload.wancfg_vlanid,
        "onu_wancfg_user_vlanid": payload.wancfg_user_vlanid,
        "onu_wancfg_cos": payload.wancfg_cos,
        "onu_wancfg_tcont": payload.wancfg_tcont,
        "onu_wancfg_gemport": payload.wancfg_gemport,
        "onu_wancfg_svlan": payload.wancfg_svlan,
        "onu_wancfg_stpid": payload.wancfg_stpid,
        "onu_wancfg_scos": payload.wancfg_scos,
        "onu_wancfg_pon_profile": payload.wancfg_pon_profile or "",
        "onu_wancfg_local_ip": payload.wancfg_local_ip or "",
    }
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/onu/apply_wan", urlencode(campos))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro(erro, resposta))
    return resposta, cpe, templates


def _nome_template(templates: list[dict[str, Any]], pk: int | None) -> str | None:
    return next((t["nome"] for t in templates if t["pk"] == pk), None)


@router.post("/{onu_pk}/associar-cliente")
async def associar_cliente_onu(
    onu_pk: int, payload: AssociarClientePayload, request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    resposta, cpe, templates = await _aplicar_wan(ctx, payload, "Não foi possível associar a ONU a este cliente.")
    # Cliente e PPPoE novos vêm da conexão já conferida (Controllr), o
    # anterior e o nome da OLT vêm da tela.
    antes = payload.auditoria or ContextoAuditoriaAssociacao()
    registrar_auditoria(request, ctx.session, "onu.associar_cliente",
                        _alvo_onu(onu_pk, payload.olt_pk, payload.port_id, payload.onu_id, antes.olt_nome,
                                  payload.onu_serial, cpe_pk=payload.cpe_pk,
                                  template=_nome_template(templates, payload.wan_tpl_pk),
                                  cliente=cpe.get("client_complete_name"), pppoe=cpe.get("cpe_username"),
                                  cliente_anterior=antes.cliente_anterior, pppoe_anterior=antes.pppoe_anterior))
    return {"success": True, "results": resposta.results}


class TemplatePayload(BaseModel):
    olt_pk: int
    onu_serial: str = Field(min_length=4, max_length=64)
    wan_tpl_pk: int
    # Só para a auditoria: o template que a tela mostrava antes.
    template_anterior: str | None = Field(default=None, max_length=128)


# Campos da config de WAN que apply_wan recebe com o mesmo nome do
# /fiber_ctl/onu/list (registro completo, ver CONTROLLR_API_NOTES 5.1).
_CAMPOS_WAN = (
    "onu_wancfg_conntype", "onu_wancfg_vlanid", "onu_wancfg_user_vlanid", "onu_wancfg_cos", "onu_wancfg_tcont",
    "onu_wancfg_gemport", "onu_wancfg_svlan", "onu_wancfg_stpid", "onu_wancfg_scos", "onu_wancfg_pon_profile",
    "onu_wancfg_pppoe_username", "onu_wancfg_pppoe_passwd", "onu_wancfg_pppoe_svcname", "onu_wancfg_local_ip",
)


def campos_troca_template(onu: dict[str, Any], wan_tpl_pk: int) -> dict[str, Any]:
    """apply_wan com o registro atual da ONU (cliente e PPPoE, se houver) e
    só o template trocado. O template são parâmetros da ONU: vai também sem
    cliente/PPPoE. Com template, PPPoE (conntype 1) e gemport 1, como as
    demais ONUs das OLTs ZTE."""
    campos: dict[str, Any] = {
        chave: onu.get(chave) for chave in ("olt_pk", "frame_id", "slot_id", "port_id", "onu_id", "onu_serial")
    }
    for chave in ("client_pk", "contract_pk", "cpe_pk"):
        campos[chave] = onu.get(chave) or 0
    for chave in _CAMPOS_WAN:
        valor = onu.get(chave)
        campos[chave] = "" if valor is None else valor
    campos["wan_tpl_pk"] = wan_tpl_pk
    campos["onu_wancfg_conntype"] = campos["onu_wancfg_conntype"] or 1
    campos["onu_wancfg_gemport"] = campos["onu_wancfg_gemport"] or 1
    return campos


@router.post("/template")
async def alterar_template_onu(
    payload: TemplatePayload, request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> dict[str, Any]:
    templates, _ = await _templates_da_olt(ctx, payload.olt_pk)
    if not templates:
        raise HTTPException(status_code=400, detail="Esta OLT não usa template de WAN.")
    if payload.wan_tpl_pk not in {t["pk"] for t in templates}:
        raise HTTPException(status_code=400, detail="Template de WAN não disponível para esta OLT.")
    # Relê a ONU no Controllr pelo serial: o registro enviado ao apply_wan
    # é o dela, não o que a tela tinha (que pode estar velho ou incompleto).
    serial = payload.onu_serial.strip().upper()
    lida = await ctx.controllr.onu_list(_corpo_busca_wizard("onu_serial", serial, sort="onu_ponid"))
    onu = next((o for o in (lida.results if lida.success else [])
                if str(o.get("onu_serial", "")).upper() == serial and o.get("olt_pk") == payload.olt_pk), None)
    if onu is None:
        raise HTTPException(status_code=404, detail="ONU não encontrada nesta OLT.")
    resposta = await ctx.controllr.call_api_post("/fiber_ctl/onu/apply_wan",
                                                 urlencode(campos_troca_template(onu, payload.wan_tpl_pk)))
    if not resposta.success:
        raise HTTPException(status_code=400, detail=detalhe_erro("Não foi possível aplicar o template na ONU.", resposta))
    registrar_auditoria(request, ctx.session, "onu.template",
                        _alvo_onu(onu.get("onu_pk") or 0, payload.olt_pk, onu.get("port_id"), onu.get("onu_id"),
                                  onu.get("olt_name"), serial, cliente=onu.get("client_name") or None,
                                  template=_nome_template(templates, payload.wan_tpl_pk),
                                  template_anterior=payload.template_anterior))
    return {"success": True, "results": resposta.results}
