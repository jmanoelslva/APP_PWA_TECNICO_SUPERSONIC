import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { LuCable, LuPlus, LuRefreshCw } from 'react-icons/lu'
import { ApiError, buscarOnu, listarOnusNaoRegistradas, provisionarOnu, type OnuNaoRegistradaDto } from '../../api/client'
import { useToast } from '../../components/Toast/useToast'
import { useFecharComVoltar } from '../../hooks/useFecharComVoltar'
import { CORES } from '../../utils/cores'

// A OLT leva cerca de 1 minuto para a ONU registrada aparecer na lista.
const INTERVALO_CONFERENCIA_MS = 10_000
const MAX_CONFERENCIAS = 18

const posicao = (onu: OnuNaoRegistradaDto) => `${onu.slot_id}/${onu.port_id}`

/**
 * ONUs ligadas na fibra e ainda não registradas (só OLTs ZTE; as VSOL
 * registram sozinhas). Tocar numa abre o registro com o nome sugerido
 * ONU_<serial>, editável; depois de registrada, a tela abre a ONU para
 * "Registrar cliente".
 */
export default function OnusAguardando({ onRegistrada }: { onRegistrada: (serial: string) => void }) {
  const [lista, setLista] = useState<OnuNaoRegistradaDto[]>([])
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [escolhida, setEscolhida] = useState<OnuNaoRegistradaDto | null>(null)

  const carregar = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      setLista((await listarOnusNaoRegistradas()).results)
    } catch (excecao) {
      setErro(excecao instanceof ApiError ? excecao.message : 'Não foi possível consultar as ONUs novas.')
    } finally {
      setCarregando(false)
    }
  }, [])

  useEffect(() => {
    carregar()
  }, [carregar])

  return (
    <section className="onu-card onu-aguardando">
      <div className="onu-aguardando-topo">
        <h2>Aguardando registro{!carregando && lista.length > 0 ? ` (${lista.length})` : ''}</h2>
        <button
          type="button"
          className="botao botao-secundario botao-quadrado"
          style={{ '--botao-cor': CORES.onu } as CSSProperties}
          onClick={carregar}
          disabled={carregando}
          aria-label="Procurar ONUs novas de novo"
          title="Procurar de novo"
        >
          <LuRefreshCw size={18} className={carregando ? 'onu-girando' : ''} />
        </button>
      </div>
      {carregando && <p className="onu-aguardando-vazio">Procurando ONUs novas nas OLTs…</p>}
      {!carregando && erro && <p className="onu-aguardando-vazio">{erro}</p>}
      {!carregando && !erro && lista.length === 0 && (
        <p className="onu-aguardando-vazio">Nenhuma ONU nova ligada na fibra. Ligue a ONU e toque em atualizar.</p>
      )}
      {!carregando && !erro && lista.length > 0 && (
        <ul className="onu-combobox-lista onu-aguardando-lista">
          {lista.map((onu) => (
            <li key={`${onu.olt_pk}-${onu.serial}`}>
              <button type="button" onClick={() => setEscolhida(onu)}>
                <LuCable size={20} aria-hidden />
                <span className="item-duas-linhas">
                  <strong>{onu.serial}</strong>
                  <small>{[onu.olt_nome, `PON ${posicao(onu)}`, onu.modelo].filter(Boolean).join(' · ')}</small>
                </span>
                <LuPlus size={20} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {escolhida && (
        <ModalProvisionar
          onu={escolhida}
          onFechar={() => setEscolhida(null)}
          onRegistrada={(serial) => {
            setEscolhida(null)
            onRegistrada(serial)
          }}
          onDesatualizada={() => {
            setEscolhida(null)
            carregar()
          }}
        />
      )}
    </section>
  )
}

function ModalProvisionar({
  onu,
  onFechar,
  onRegistrada,
  onDesatualizada,
}: {
  onu: OnuNaoRegistradaDto
  onFechar: () => void
  onRegistrada: (serial: string) => void
  onDesatualizada: () => void
}) {
  const { toast } = useToast()
  const [nome, setNome] = useState(`ONU_${onu.serial}`)
  const [etapa, setEtapa] = useState<'editar' | 'registrando' | 'aguardando'>('editar')
  const ativo = useRef(true)
  useEffect(
    () => () => {
      ativo.current = false
    },
    [],
  )
  // Registrando não fecha: a resposta ainda não veio. Aguardando pode
  // fechar — a ONU já foi registrada, só não apareceu na lista ainda.
  useFecharComVoltar(true, () => {
    if (etapa !== 'registrando') onFechar()
  })

  async function aguardarNaOlt() {
    for (let i = 0; i < MAX_CONFERENCIAS && ativo.current; i++) {
      await new Promise((resolver) => setTimeout(resolver, INTERVALO_CONFERENCIA_MS))
      if (!ativo.current) return
      try {
        const resposta = await buscarOnu({ serial: onu.serial })
        if (resposta.results.some((r) => r.sn?.toUpperCase() === onu.serial)) {
          toast('ONU registrada. Agora registre o cliente nela.', 'sucesso')
          onRegistrada(onu.serial)
          return
        }
      } catch {
        // Sinal fraco numa das conferências: tenta de novo na próxima.
      }
    }
    if (ativo.current) {
      toast('A OLT ainda não confirmou o registro. Busque pelo serial em alguns minutos.', 'info')
      onFechar()
    }
  }

  async function registrar() {
    setEtapa('registrando')
    try {
      await provisionarOnu({
        olt_pk: onu.olt_pk,
        slot_id: onu.slot_id,
        port_id: onu.port_id,
        onu_serial: onu.serial,
        onu_name: nome.trim(),
      })
      setEtapa('aguardando')
      aguardarNaOlt()
    } catch (excecao) {
      if (excecao instanceof ApiError && excecao.status === 409) {
        toast(excecao.message)
        onDesatualizada()
        return
      }
      toast(excecao instanceof ApiError ? excecao.message : 'Não foi possível registrar a ONU.')
      setEtapa('editar')
    }
  }

  return (
    <div className="onu-modal-fundo" onClick={() => etapa === 'editar' && onFechar()}>
      <div className="onu-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Registrar ONU na OLT</h2>
        <p className="onu-modal-texto">
          <strong>{onu.serial}</strong>
          <br />
          {[onu.olt_nome, `PON ${posicao(onu)}`, onu.modelo].filter(Boolean).join(' · ')}
        </p>
        {etapa === 'aguardando' ? (
          <p className="onu-modal-texto onu-aguardando-olt">
            <LuRefreshCw size={16} className="onu-girando" aria-hidden /> Registrada. Aguardando a OLT confirmar (cerca de
            1 minuto)…
          </p>
        ) : (
          <>
            <label className="onu-aguardando-rotulo" htmlFor="onu-provisionar-nome">
              Nome da ONU
            </label>
            <input
              id="onu-provisionar-nome"
              type="text"
              className="onu-modal-input"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              maxLength={64}
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="done"
              disabled={etapa === 'registrando'}
            />
          </>
        )}
        <div className="onu-modal-acoes">
          <button className="botao botao-secundario" onClick={onFechar} disabled={etapa === 'registrando'}>
            {etapa === 'aguardando' ? 'Fechar' : 'Cancelar'}
          </button>
          {etapa !== 'aguardando' && (
            <button
              aria-busy={etapa === 'registrando'}
              className="botao botao-primario"
              disabled={etapa === 'registrando' || !nome.trim()}
              onClick={registrar}
            >
              {etapa === 'registrando' ? 'Registrando…' : 'Registrar'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
