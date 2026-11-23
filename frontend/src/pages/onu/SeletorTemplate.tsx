import { useEffect, useState } from 'react'
import { listarTemplatesWan, type TemplateWanDto } from '../../api/client'

/**
 * Templates de WAN da OLT para escolher (perfil do cliente). Começa no
 * `inicial` ou, sem ele, no padrão da OLT. Nas OLTs sem template (VSOL)
 * não mostra nada e não chama onEscolher.
 */
export function useTemplatesWan(oltPk: number | undefined, inicial: number | undefined) {
  const [templates, setTemplates] = useState<TemplateWanDto[]>([])
  const [escolhido, setEscolhido] = useState<number | undefined>(inicial || undefined)
  const [carregando, setCarregando] = useState(oltPk != null)

  useEffect(() => {
    if (oltPk == null) return
    let cancelado = false
    listarTemplatesWan(oltPk)
      .then((resposta) => {
        if (cancelado) return
        setTemplates(resposta.results)
        const existe = (pk?: number) => !!pk && resposta.results.some((t) => t.pk === pk)
        setEscolhido((atual) => (existe(atual) ? atual : (resposta.padrao ?? resposta.results[0]?.pk)))
      })
      .catch(() => {
        // Sem a lista, o servidor usa o template da OLT.
        if (!cancelado) setTemplates([])
      })
      .finally(() => {
        if (!cancelado) setCarregando(false)
      })
    return () => {
      cancelado = true
    }
  }, [oltPk])

  return { templates, escolhido, setEscolhido, carregando }
}

export default function SeletorTemplate({
  templates,
  escolhido,
  onEscolher,
  desabilitado,
}: {
  templates: TemplateWanDto[]
  escolhido: number | undefined
  onEscolher: (pk: number) => void
  desabilitado?: boolean
}) {
  if (templates.length === 0) return null
  return (
    <>
      <label className="onu-aguardando-rotulo" htmlFor="onu-template-wan">
        Template de WAN
      </label>
      <select
        id="onu-template-wan"
        className="campo onu-template-select"
        value={escolhido ?? ''}
        onChange={(e) => onEscolher(Number(e.target.value))}
        disabled={desabilitado}
      >
        {templates.map((t) => (
          <option key={t.pk} value={t.pk}>
            {t.nome}
          </option>
        ))}
      </select>
    </>
  )
}
