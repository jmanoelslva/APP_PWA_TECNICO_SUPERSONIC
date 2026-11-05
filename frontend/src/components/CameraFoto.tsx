import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { MdCameraswitch, MdCheck, MdClose, MdPhotoCamera, MdPhotoLibrary, MdReplay } from 'react-icons/md'
import './CameraFoto.css'

interface Props {
  titulo: string
  onFoto: (arquivo: File) => void
  onFechar: () => void
}

type Lado = 'environment' | 'user'

/**
 * Câmera em tela cheia dentro do app, no jeito do WhatsApp: botão de tirar
 * a foto, galeria no canto (abre o seletor do telefone) e virar a câmera.
 * Depois da foto, "Usar" ou "Tirar outra". Sem câmera (permissão negada ou
 * aparelho sem suporte), oferece a câmera do próprio telefone e a galeria.
 *
 * O navegador não dá acesso livre à galeria: as miniaturas das últimas
 * fotos (como no WhatsApp) só existem em app nativo.
 */
export default function CameraFoto({ titulo, onFoto, onFechar }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const inputGaleriaRef = useRef<HTMLInputElement>(null)
  const inputCameraRef = useRef<HTMLInputElement>(null)
  const [lado, setLado] = useState<Lado>('environment')
  const [erro, setErro] = useState<string | null>(null)
  const [pronta, setPronta] = useState(false)
  // Foto tirada, aguardando "Usar" ou "Tirar outra".
  const [tirada, setTirada] = useState<{ blob: Blob; url: string } | null>(null)

  useEffect(() => {
    let cancelado = false
    setPronta(false)

    async function iniciar() {
      if (!navigator.mediaDevices?.getUserMedia) {
        setErro('Este navegador não abre a câmera dentro do app.')
        return
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: lado }, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        })
        if (cancelado) {
          stream.getTracks().forEach((faixa) => faixa.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play()
        }
        setPronta(true)
      } catch (excecao) {
        if (cancelado) return
        const negada = excecao instanceof DOMException && excecao.name === 'NotAllowedError'
        setErro(negada ? 'Permissão de câmera negada para o app.' : 'Não foi possível abrir a câmera.')
      }
    }

    void iniciar()
    return () => {
      cancelado = true
      streamRef.current?.getTracks().forEach((faixa) => faixa.stop())
      streamRef.current = null
    }
  }, [lado])

  // Libera a prévia ao trocar ou fechar.
  useEffect(() => () => {
    if (tirada) URL.revokeObjectURL(tirada.url)
  }, [tirada])

  function tirarFoto() {
    const video = videoRef.current
    if (!video || !video.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')?.drawImage(video, 0, 0)
    canvas.toBlob(
      (blob) => {
        if (blob) setTirada({ blob, url: URL.createObjectURL(blob) })
      },
      'image/jpeg',
      0.9,
    )
  }

  function usarFoto() {
    if (!tirada) return
    onFoto(new File([tirada.blob], `foto-${Date.now()}.jpg`, { type: 'image/jpeg' }))
  }

  function aoEscolherArquivo(evento: ChangeEvent<HTMLInputElement>) {
    const arquivo = evento.target.files?.[0]
    evento.target.value = ''
    if (arquivo) onFoto(arquivo)
  }

  return (
    <div className="camera-foto">
      <input ref={inputGaleriaRef} type="file" accept="image/*" hidden onChange={aoEscolherArquivo} />
      <input ref={inputCameraRef} type="file" accept="image/*" capture="environment" hidden onChange={aoEscolherArquivo} />

      <div className="camera-foto-topo">
        <span>{titulo}</span>
        <button type="button" className="camera-foto-icone" onClick={onFechar} aria-label="Fechar câmera">
          <MdClose size={24} />
        </button>
      </div>

      {erro ? (
        <div className="camera-foto-aviso">
          <p>{erro}</p>
          <button type="button" className="botao botao-primario" onClick={() => inputCameraRef.current?.click()}>
            <MdPhotoCamera size={18} /> Câmera do telefone
          </button>
          <button type="button" className="botao botao-secundario" onClick={() => inputGaleriaRef.current?.click()}>
            <MdPhotoLibrary size={18} /> Galeria
          </button>
        </div>
      ) : (
        <>
          <video
            ref={videoRef}
            className={`camera-foto-video${lado === 'user' ? ' camera-foto-espelho' : ''}`}
            muted
            playsInline
            hidden={!!tirada}
          />
          {tirada && <img src={tirada.url} alt="Foto tirada" className="camera-foto-video" />}

          <div className="camera-foto-base">
            {tirada ? (
              <>
                <button type="button" className="camera-foto-acao" onClick={() => setTirada(null)}>
                  <MdReplay size={22} /> Tirar outra
                </button>
                <button type="button" className="camera-foto-acao camera-foto-usar" onClick={usarFoto}>
                  <MdCheck size={22} /> Usar
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="camera-foto-icone camera-foto-redondo"
                  onClick={() => inputGaleriaRef.current?.click()}
                  aria-label="Escolher da galeria"
                >
                  <MdPhotoLibrary size={26} />
                </button>
                <button
                  type="button"
                  className="camera-foto-disparo"
                  disabled={!pronta}
                  onClick={tirarFoto}
                  aria-label="Tirar foto"
                />
                <button
                  type="button"
                  className="camera-foto-icone camera-foto-redondo"
                  onClick={() => setLado((atual) => (atual === 'environment' ? 'user' : 'environment'))}
                  aria-label="Virar a câmera"
                >
                  <MdCameraswitch size={26} />
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
