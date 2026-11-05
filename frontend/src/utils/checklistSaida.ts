/**
 * Fotos da OS conforme a Tarefa, dispensas com motivo e as linhas que entram
 * prontas na descrição da finalização (dá para editar). Tudo fica salvo no
 * aparelho por OS, para não perder ao sair e voltar.
 */

/** Leitura do sinal da ONU para a descrição; vazio sem leitura. */
export function textoSinal(rx?: number | null): string {
  return rx != null ? `Sinal da ONU: ${rx.toLocaleString('pt-BR')} dBm.` : ''
}

/**
 * Fotos da OS, conforme a Tarefa (task_pk do Controllr): instalação de
 * fibra e mudança de endereço exigem as 7 da fibra; desinstalação, os
 * equipamentos recolhidos; as demais tarefas mostram as da fibra como
 * opcionais. Cada foto sobe para o chamado com o nome da etapa (ex.:
 * "cto-aberta-123.jpg"). Sem uma foto obrigatória, a OS só finaliza se ela
 * for dispensada com motivo.
 */
export interface FotoObrigatoria {
  chave: string
  rotulo: string
}

export const FOTOS_FIBRA: FotoObrigatoria[] = [
  { chave: 'cto-aberta', rotulo: 'CTO aberta' },
  { chave: 'cto-fechada', rotulo: 'CTO fechada' },
  { chave: 'atenuacao-onu', rotulo: 'Atenuação na ONU' },
  { chave: 'atenuacao-cto', rotulo: 'Atenuação na CTO' },
  { chave: 'instalacao', rotulo: 'Instalação concluída' },
  { chave: 'mac-roteador', rotulo: 'MAC do roteador' },
  { chave: 'mac-onu', rotulo: 'MAC da ONU' },
]

export const FOTOS_DESINSTALACAO: FotoObrigatoria[] = [
  { chave: 'onu-recolhida', rotulo: 'ONU recolhida (com etiqueta)' },
  { chave: 'roteador-recolhido', rotulo: 'Roteador recolhido (com etiqueta)' },
]

// Tarefas do Controllr SUPERSONIC (task_pk): 2.2 Instalação Fibra,
// 3.1 Mudança de Endereço e Mudança de Endereço; 5.1 Desinstalação Antena
// e 5.2 Desinstalação Equipamento. 2.1 Instalação Rádio fica sem exigência.
const TAREFAS_FIBRA = [22, 31, 54]
const TAREFAS_DESINSTALACAO = [51, 52]

export function fotosDaTarefa(taskPk?: number | null): { fotos: FotoObrigatoria[]; obrigatorias: boolean } {
  if (taskPk != null && TAREFAS_FIBRA.includes(taskPk)) return { fotos: FOTOS_FIBRA, obrigatorias: true }
  if (taskPk != null && TAREFAS_DESINSTALACAO.includes(taskPk)) return { fotos: FOTOS_DESINSTALACAO, obrigatorias: true }
  return { fotos: FOTOS_FIBRA, obrigatorias: false }
}

export const MOTIVOS_DISPENSA = ['CTO sem acesso', 'Cliente ausente', 'Equipamento do cliente', 'Sem equipamento no local', 'Outro']

/** Foto obrigatória que o técnico não conseguiu tirar: chave → motivo. */
export type FotosDispensadas = Record<string, string>

const PREFIXO_DISPENSAS = 'supersonic-tecnico-fotos-dispensadas-'

export function lerDispensas(osPk: number): FotosDispensadas {
  try {
    const dados = JSON.parse(localStorage.getItem(PREFIXO_DISPENSAS + osPk) ?? '{}') as unknown
    if (!dados || typeof dados !== 'object' || Array.isArray(dados)) return {}
    return Object.fromEntries(Object.entries(dados).filter(([, motivo]) => typeof motivo === 'string' && motivo.trim()))
  } catch {
    return {}
  }
}

export function salvarDispensas(osPk: number, dispensas: FotosDispensadas): void {
  try {
    if (Object.keys(dispensas).length) localStorage.setItem(PREFIXO_DISPENSAS + osPk, JSON.stringify(dispensas))
    else localStorage.removeItem(PREFIXO_DISPENSAS + osPk)
  } catch {
    // sem armazenamento: vale só nesta tela
  }
}

/** Linha para a descrição da finalização com as fotos dispensadas e o motivo. */
export function textoDispensas(lista: FotoObrigatoria[], dispensas: FotosDispensadas): string {
  const partes = lista.filter((foto) => dispensas[foto.chave]).map((foto) => `${foto.rotulo} (${dispensas[foto.chave]})`)
  return partes.length ? `Fotos não tiradas: ${partes.join('; ')}.` : ''
}

/** Foto anexada: chave → id na fila de anexos (null quando já foi enviada). */
export type FotosAnexadas = Record<string, number | null>

const PREFIXO_FOTOS = 'supersonic-tecnico-fotos-'

export function lerFotos(osPk: number): FotosAnexadas {
  try {
    const dados = JSON.parse(localStorage.getItem(PREFIXO_FOTOS + osPk) ?? '{}') as unknown
    if (!dados || typeof dados !== 'object' || Array.isArray(dados)) return {}
    const fotos: FotosAnexadas = {}
    for (const [chave, valor] of Object.entries(dados)) {
      if (valor === null || typeof valor === 'number') fotos[chave] = valor
    }
    return fotos
  } catch {
    return {}
  }
}

export function salvarFotos(osPk: number, fotos: FotosAnexadas): void {
  try {
    if (Object.keys(fotos).length) localStorage.setItem(PREFIXO_FOTOS + osPk, JSON.stringify(fotos))
    else localStorage.removeItem(PREFIXO_FOTOS + osPk)
  } catch {
    // sem armazenamento: vale só nesta tela
  }
}

/** Fotos da lista sem anexo nem dispensa, na ordem da lista. */
export function fotosFaltando(
  lista: FotoObrigatoria[],
  fotos: FotosAnexadas,
  dispensas: FotosDispensadas = {},
): FotoObrigatoria[] {
  return lista.filter((foto) => !(foto.chave in fotos) && !dispensas[foto.chave])
}

/** Nome do arquivo no chamado: etapa + número da OS, com a extensão original. */
export function nomeArquivoFoto(chave: string, numeroOs: string | number, nomeOriginal: string): string {
  const extensao = /\.[a-z0-9]{2,5}$/i.exec(nomeOriginal)?.[0].toLowerCase() ?? '.jpg'
  return `${chave}-${numeroOs}${extensao}`
}
