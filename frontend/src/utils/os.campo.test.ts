import { beforeEach, describe, expect, it } from 'vitest'
import { ApiError, SessaoExpiradaError } from '../api/client'
import {
  FOTOS_DESINSTALACAO,
  FOTOS_FIBRA,
  fotosDaTarefa,
  fotosFaltando,
  lerDispensas,
  lerFotos,
  nomeArquivoFoto,
  salvarDispensas,
  salvarFotos,
  textoSinal,
  textoDispensas,
} from './checklistSaida'
import { linhasCarimbo } from './carimbo'
import { decidirAposFalha, ehFalhaDeRede } from './filaAnexos'
import { deveComprimir, dimensoesReduzidas } from './imagem'

describe('dimensoesReduzidas', () => {
  it('reduz pelo lado maior sem distorcer', () => {
    expect(dimensoesReduzidas(4000, 3000)).toEqual({ largura: 1600, altura: 1200 })
    expect(dimensoesReduzidas(3000, 4000)).toEqual({ largura: 1200, altura: 1600 })
  })

  it('nunca amplia', () => {
    expect(dimensoesReduzidas(800, 600)).toEqual({ largura: 800, altura: 600 })
  })
})

describe('deveComprimir', () => {
  it('só imagem comum e grande', () => {
    expect(deveComprimir({ type: 'image/jpeg', size: 3_000_000 })).toBe(true)
    expect(deveComprimir({ type: 'image/jpeg', size: 100_000 })).toBe(false)
    expect(deveComprimir({ type: 'application/pdf', size: 3_000_000 })).toBe(false)
    expect(deveComprimir({ type: 'image/heic', size: 3_000_000 })).toBe(false)
  })
})

describe('fila de anexos', () => {
  it('mantém sem rede, sessão expirada ou erro do servidor', () => {
    expect(decidirAposFalha(new TypeError('Failed to fetch'))).toBe('manter')
    expect(decidirAposFalha(new SessaoExpiradaError())).toBe('manter')
    expect(decidirAposFalha(new ApiError('falhou', 502))).toBe('manter')
  })

  it('descarta o que o servidor recusou', () => {
    expect(decidirAposFalha(new ApiError('grande demais', 413))).toBe('descartar')
  })

  it('reconhece falha de rede', () => {
    expect(ehFalhaDeRede(new TypeError('Failed to fetch'))).toBe(true)
    expect(ehFalhaDeRede(new ApiError('x', 400))).toBe(false)
    expect(ehFalhaDeRede(new SessaoExpiradaError())).toBe(false)
  })
})

describe('sinal na descrição', () => {
  it('com e sem leitura', () => {
    expect(textoSinal(-19.8)).toBe('Sinal da ONU: -19,8 dBm.')
    expect(textoSinal(null)).toBe('')
  })
})

describe('fotos obrigatórias', () => {
  beforeEach(() => localStorage.clear())

  it('lista o que falta, na ordem; dispensa com motivo conta', () => {
    expect(fotosFaltando(FOTOS_FIBRA, {})).toHaveLength(FOTOS_FIBRA.length)
    const todas = Object.fromEntries(FOTOS_FIBRA.map((f) => [f.chave, null]))
    expect(fotosFaltando(FOTOS_FIBRA, todas)).toEqual([])
    const { 'mac-onu': _, ...semMacOnu } = todas
    expect(fotosFaltando(FOTOS_FIBRA, semMacOnu).map((f) => f.chave)).toEqual(['mac-onu'])
    expect(fotosFaltando(FOTOS_FIBRA, semMacOnu, { 'mac-onu': 'Cliente ausente' })).toEqual([])
  })

  it('escolhe as fotos pela Tarefa', () => {
    expect(fotosDaTarefa(22)).toEqual({ fotos: FOTOS_FIBRA, obrigatorias: true })
    expect(fotosDaTarefa(31).obrigatorias).toBe(true)
    expect(fotosDaTarefa(54).obrigatorias).toBe(true)
    expect(fotosDaTarefa(52)).toEqual({ fotos: FOTOS_DESINSTALACAO, obrigatorias: true })
    expect(fotosDaTarefa(51).fotos).toBe(FOTOS_DESINSTALACAO)
    expect(fotosDaTarefa(21).obrigatorias).toBe(false)
    expect(fotosDaTarefa(57).obrigatorias).toBe(false)
    expect(fotosDaTarefa(undefined).obrigatorias).toBe(false)
  })

  it('texto e armazenamento das dispensas', () => {
    expect(textoDispensas(FOTOS_FIBRA, { 'mac-onu': 'Cliente ausente', 'cto-aberta': 'CTO sem acesso' })).toBe(
      'Fotos não tiradas: CTO aberta (CTO sem acesso); MAC da ONU (Cliente ausente).',
    )
    expect(textoDispensas(FOTOS_FIBRA, {})).toBe('')
    salvarDispensas(10, { 'cto-aberta': 'CTO sem acesso', vazio: ' ' })
    expect(lerDispensas(10)).toEqual({ 'cto-aberta': 'CTO sem acesso' })
    salvarDispensas(10, {})
    expect(lerDispensas(10)).toEqual({})
  })

  it('carimbo com OS, data e GPS', () => {
    const quando = new Date(2026, 10, 5, 9, 7)
    const [linha1, linha2] = linhasCarimbo(123, quando, { lat: -10.9123456, lng: -37.0654321 })
    expect(linha1).toMatch(/^OS 123 · 05\/11\/2026,? 09:07$/)
    expect(linha2).toBe('-10.912346, -37.065432')
    expect(linhasCarimbo(123, quando, null)).toHaveLength(1)
  })

  it('guarda por OS, inclusive o id da fila', () => {
    salvarFotos(10, { 'cto-aberta': null, 'cto-fechada': 7 })
    expect(lerFotos(10)).toEqual({ 'cto-aberta': null, 'cto-fechada': 7 })
    expect(lerFotos(11)).toEqual({})
    salvarFotos(10, {})
    expect(lerFotos(10)).toEqual({})
  })

  it('nomeia o arquivo pela etapa', () => {
    expect(nomeArquivoFoto('cto-aberta', 55, 'IMG_2026.JPEG')).toBe('cto-aberta-55.jpeg')
    expect(nomeArquivoFoto('mac-onu', 55, 'foto')).toBe('mac-onu-55.jpg')
  })
})
