/**
 * Carimbo nas fotos da OS: data e hora, número da OS e posição do GPS
 * gravados no canto da imagem, para reclamação e auditoria. A foto também
 * sai reduzida (mesmo lado máximo de utils/imagem.ts). Qualquer falha
 * devolve o arquivo original — carimbar nunca impede o envio.
 */
import { LADO_MAXIMO, dimensoesReduzidas } from './imagem'

const TIPOS = ['image/jpeg', 'image/png', 'image/webp']

export interface Posicao {
  lat: number
  lng: number
}

/**
 * Linhas do carimbo: OS e data/hora; depois a posição, quando houver.
 * `numeroOs` é o número que a tela mostra (op_number), não o id interno.
 */
export function linhasCarimbo(numeroOs: string | number, quando: Date, posicao?: Posicao | null): string[] {
  const data = quando.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
  const linhas = [`OS ${numeroOs} · ${data}`]
  if (posicao) linhas.push(`${posicao.lat.toFixed(6)}, ${posicao.lng.toFixed(6)}`)
  return linhas
}

/** Posição atual em até 6 s (aceita leitura de até 2 min); null sem GPS. */
export function posicaoRapida(): Promise<Posicao | null> {
  return new Promise((resolver) => {
    if (!navigator.geolocation) {
      resolver(null)
      return
    }
    navigator.geolocation.getCurrentPosition(
      (p) => resolver({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => resolver(null),
      { enableHighAccuracy: true, timeout: 6000, maximumAge: 120000 },
    )
  })
}

export async function carimbarImagem(arquivo: File, linhas: string[]): Promise<File> {
  if (!TIPOS.includes(arquivo.type) || typeof createImageBitmap !== 'function') return arquivo
  try {
    const bitmap = await createImageBitmap(arquivo, { imageOrientation: 'from-image' })
    const { largura, altura } = dimensoesReduzidas(bitmap.width, bitmap.height, LADO_MAXIMO)
    const canvas = document.createElement('canvas')
    canvas.width = largura
    canvas.height = altura
    const contexto = canvas.getContext('2d')
    if (!contexto) return arquivo
    contexto.drawImage(bitmap, 0, 0, largura, altura)
    bitmap.close()

    // Faixa escura no rodapé, texto branco proporcional à foto.
    const fonte = Math.max(14, Math.round(Math.min(largura, altura) * 0.035))
    const margem = Math.round(fonte * 0.6)
    const alturaFaixa = linhas.length * fonte * 1.3 + margem * 2
    contexto.fillStyle = 'rgba(0, 0, 0, 0.55)'
    contexto.fillRect(0, altura - alturaFaixa, largura, alturaFaixa)
    contexto.fillStyle = '#fff'
    contexto.font = `600 ${fonte}px sans-serif`
    contexto.textBaseline = 'top'
    linhas.forEach((linha, indice) => {
      contexto.fillText(linha, margem, altura - alturaFaixa + margem + indice * fonte * 1.3)
    })

    const blob = await new Promise<Blob | null>((resolver) => canvas.toBlob(resolver, 'image/jpeg', 0.85))
    if (!blob) return arquivo
    const nome = arquivo.name.replace(/\.[^.]+$/, '') + '.jpg'
    return new File([blob], nome, { type: 'image/jpeg', lastModified: arquivo.lastModified })
  } catch {
    return arquivo
  }
}
