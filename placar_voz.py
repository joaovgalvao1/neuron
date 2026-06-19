# PLACAR NEURON — Voz + HTML + Arduino

import os, glob, json, logging, queue, random, threading, time, unicodedata, warnings, webbrowser
from collections import deque

import numpy as np
import serial
from serial.tools import list_ports
import sounddevice as sd
from flask import Flask, jsonify, send_file
from rapidfuzz import fuzz, process


def _registrar_dlls_cuda():
    # Coloca as DLLs do CUDA (cuBLAS/cuDNN, instaladas via pip) no caminho de busca,
    # p/ o CTranslate2 usar a GPU. Sem as libs nvidia-*, o app simplesmente roda em CPU.
    try:
        import nvidia
    except ImportError:
        return
    raiz = list(nvidia.__path__)[0]
    for pasta in {os.path.dirname(p) for p in glob.glob(os.path.join(raiz, "**", "*.dll"), recursive=True)}:
        os.environ["PATH"] = pasta + os.pathsep + os.environ["PATH"]
        try:
            os.add_dll_directory(pasta)
        except OSError:
            pass


_registrar_dlls_cuda()  # precisa rodar ANTES de importar o faster_whisper (que carrega o CTranslate2)
from faster_whisper import WhisperModel

warnings.filterwarnings("ignore")
# O Flask loga cada requisição; como o HTML consulta /estado a cada 300ms, isso
# encheria o terminal. Mantém só os erros reais.
logging.getLogger("werkzeug").setLevel(logging.ERROR)


# ============================== CONFIGURAÇÕES ==============================
# Modelo de voz na GPU NVIDIA (máxima precisão). 
# Sem placa de vídeo, troque por: "small", "cpu", "int8". -> aperte CRTL + F, busque por beam_size e altere de 5 para 1
# Outras opções de precisão x peso: "medium"/"small"/"base"/"tiny" - trocar no WHISPER_MODEL
WHISPER_MODEL, WHISPER_DEVICE, COMPUTE_TYPE = "large-v3", "cuda", "float16"
SERIAL_PORT, BAUD_RATE = "COM8", 9600        # o código detecta a porta sozinho; se a detecção falhar, usa esta porta fixa
SAMPLERATE, FLASK_PORT = 16000, 8080         

# Detector de fala caseiro. Cada bloco dura ~0.25s (BLOCKSIZE/SAMPLERATE).
SILENCIO_LIMIAR = 350    # volume médio, abaixo disso = silêncio
SILENCIO_BLOCOS = 4      # silêncio contínuo (~1s) que encerra a fala
MIN_BLOCOS_FALA = 3      # falas mais curtas que isso (~0.75s) são ruído e são ignoradas
PRE_ROLL_BLOCOS = 3      # blocos guardados ANTES da fala, para não cortar o início
BLOCKSIZE       = 4000   # amostras por bloco de áudio


# ============================== ESTADO DO JOGO ==============================
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(DIRETORIO_ATUAL, "enigmas.json"), encoding="utf-8") as f:
    ENIGMAS = json.load(f)
random.shuffle(ENIGMAS)  # ordem aleatória a cada execução do jogo

# tela: "pergunta" ou "resposta". feedback/feedback_contador: sinalizam erro para a animação no HTML.
# menu: True abre o painel de comandos na página (controlável por voz).
# voz: "carregando" (subindo o modelo/microfone) -> "ouvindo" (pronto) -> "erro" (microfone falhou).
# ativo: quando False, o reconhecimento ignora tudo exceto o comando de "ativar microfone".
# encerrar: False / "confirmando" (esperando confirmação na tela) / "encerrando" (fechando).
estado = {"enigma_index": 0, "tela": "pergunta", "total": len(ENIGMAS), "feedback": None, "feedback_contador": 0,
          "menu": False, "voz": "carregando", "ativo": True, "encerrar": False}


# ============================== COMANDOS DE VOZ ==============================
def limpar_texto(t):
    # Normaliza para comparação: remove acentos, baixa a caixa e tira espaços das pontas.
    return ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn').lower().strip() if t else ""

# Chaves já em forma normalizada: limpar_texto cuida da fala recebida.
COMANDOS_ENIGMA = {
    "proxima pergunta": "proximo", "proximo pergunta": "proximo",
    "proxima enigma": "proximo",   "proximo enigma": "proximo",
    "enigma seguinte": "proximo",
    "mostrar resposta": "resposta", "mostrar a resposta": "resposta", "revelar resposta": "resposta", "revelar a resposta": "resposta",
    "mostrar pergunta": "pergunta", "mostrar enigma": "pergunta",
    "enigma anterior": "anterior",
    # abrir/fechar o painel de comandos na tela
    "abrir comandos": "menu_abrir", "mostrar comandos": "menu_abrir", "abrir menu": "menu_abrir", "mostrar menu": "menu_abrir",
    "fechar comandos": "menu_fechar", "esconder comandos": "menu_fechar", "fechar menu": "menu_fechar", "esconder menu": "menu_fechar",
}

# Cada frase mapeia para a letra que o firmware do Arduino espera (maiúscula/minúscula importam).
COMANDOS_ARDUINO = {
    "ponto vermelho": 'R', "ponto verde": 'G', "ponto azul": 'B', "ponto amarelo": 'Y',
    "dois pontos vermelho": 'Q', "2 pontos vermelho": 'Q', "dois pontos verde": 'H', "2 pontos verde": 'H',
    "dois pontos azul": 'C', "2 pontos azul": 'C', "dois pontos amarelo": 'Z', "2 pontos amarelo": 'Z',
    "menos ponto vermelho": 'r', "menos ponto verde": 'g', "menos ponto azul": 'b', "menos ponto amarelo": 'y',
    "desligar vermelho": 'w', "desligar verde": 'h', "desligar azul": 'c', "desligar amarelo": 'z', "desligar tudo": 'X',
}

# Ativar/desativar o reconhecimento. True = ativa; False = silencia os comandos.
COMANDOS_MIC = {
    "ativar microfone": True, "ativar comandos": True, "ativar voz": True,
    "ligar microfone": True, "ligar comandos": True, "ativar reconhecimento": True,
    "desativar microfone": False, "desativar comandos": False, "desativar voz": False,
    "pausar microfone": False, "pausar comandos": False, "silenciar microfone": False,
}

# Comando único para ENCERRAR a aplicação, sem fuzzy, pra nunca disparar por engano. 
# Pede confirmação na tela antes de encerrar.
COMANDOS_ENCERRAR = (
    "encerrar aplicacao", "encerrar a aplicacao", "encerrar programa", "encerrar o programa"
)

# Formas de dizer uma alternativa. Casadas como TRECHO da fala. Inclui erros comuns
MAPA_FRASES = {
    "alternativa a": "a", "letra a": "a", "opção a": "a",
    "alternativa b": "b", "letra b": "b", "opção b": "b", "letra bê": "b", "resposta bê": "b",
    "alternativa c": "c", "letra c": "c", "opção c": "c", "letra cê": "c", "resposta cê": "c",
    "alternativa d": "d", "letra d": "d", "opção d": "d", "letra dê": "d", "resposta dê": "d",
    "litra a": "a", "litra b": "b", "litra c": "c", "litra d": "d",
}

# Pré-normaliza e ordena (mais longas primeiro, p/ a frase específica vencer a genérica).
# Feito uma vez aqui em vez de a cada fala dentro do loop de reconhecimento.
ENIGMA_ORDENADO  = sorted(((limpar_texto(f), a) for f, a in COMANDOS_ENIGMA.items()), key=lambda x: len(x[0]), reverse=True)
ARDUINO_ORDENADO = sorted(((limpar_texto(f), c) for f, c in COMANDOS_ARDUINO.items()), key=lambda x: len(x[0]), reverse=True)
FRASES_ORDENADO  = sorted(((limpar_texto(f), l) for f, l in MAPA_FRASES.items()), key=lambda x: len(x[0]), reverse=True)

MIC_ORDENADO = sorted(((limpar_texto(f), a) for f, a in COMANDOS_MIC.items()), key=lambda x: len(x[0]), reverse=True)

# Listas só com as frases (para a busca por similaridade).
ENIGMA_FRASES  = [f for f, _ in ENIGMA_ORDENADO]
ARDUINO_FRASES = [f for f, _ in ARDUINO_ORDENADO]
MIC_FRASES     = [f for f, _ in MIC_ORDENADO]

LIMIAR_FUZZY = 85  # similaridade mínima (0-100) p/ aceitar um comando aproximado


def casar_fuzzy(texto, frases, mapa_ordenado):
    # Acha o comando mais PARECIDO com a fala (tolera variações da transcrição, 
    # ex.: 'mostra a resposta' = 'mostrar resposta'.
    # Retorna o valor associado, ou None se nada passar do limiar.
    melhor = process.extractOne(texto, frases, scorer=fuzz.WRatio, score_cutoff=LIMIAR_FUZZY)
    if melhor is None:
        return None
    frase, score, idx = melhor
    print(f"[VOZ] ~ '{frase}' ({score:.0f}% de similaridade)")
    return mapa_ordenado[idx][1]


def detectar_letra(texto):
    # Extrai A/B/C/D do texto já normalizado. Retorna a letra minúscula ou None.
    for frase, letra in FRASES_ORDENADO:
        if frase in texto:
            return letra
    return None


# ============================== ARDUINO ==============================
arduino = None

# Pistas para reconhecer a porta do Arduino entre as portas USB do PC.
ARDUINO_VIDS   = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}  # Arduino oficial, CH340, FTDI, CP210x (clones)
ARDUINO_PISTAS = ("arduino", "ch340", "ch341", "usb-serial", "usb serial", "wch", "cp210", "ftdi")

def detectar_porta_arduino():
    # Procura a porta do Arduino automaticamente. Retorna o nome (ex.: 'COM8') ou None.
    portas = list(list_ports.comports())
    # 1) Casa por VID conhecido ou por palavra típica na descrição/fabricante
    for p in portas:
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if p.vid in ARDUINO_VIDS or any(k in desc for k in ARDUINO_PISTAS):
            return p.device
    # 2) Se só existe uma porta serial no PC, assume que é o Arduino
    if len(portas) == 1:
        return portas[0].device
    return None

def conectar_arduino():
    # Conecta ao Arduino na porta detectada (ou no SERIAL_PORT fixo). Sem placa, segue offline.
    global arduino
    porta = detectar_porta_arduino() or SERIAL_PORT
    try:
        arduino = serial.Serial(porta, BAUD_RATE, timeout=1)
        time.sleep(2)  # o Arduino reinicia ao abrir a serial; espera ele subir
        print(f"[OK] Arduino conectado na porta {porta}.")
    except Exception:
        print(f"[AVISO] Arduino não detectado (tentou {porta}). Iniciando em modo offline.")

def enviar_arduino(cmd):
    if arduino and arduino.is_open:
        arduino.write(cmd.encode()); print(f"[LED] Comando '{cmd}' enviado.")
    else:
        print(f"[LED] Ignorado (Arduino offline) - '{cmd}'")


# ============================== LÓGICA DO JOGO ==============================
def limpar_feedback(): estado["feedback"] = None

def encerrar_app():
    # Fecha o Arduino e encerra o processo inteiro (todas as threads).
    print("[SISTEMA] Encerrando a aplicação...")
    try:
        if arduino and arduino.is_open:
            arduino.close()
    except Exception:
        pass
    os._exit(0)

def processar_texto(texto):
    # Interpreta uma fala transcrita e aplica o comando correspondente, se houver.
    texto_limpo = limpar_texto(texto)
    print(f"\n[VOZ] Capturado: '{texto_limpo}'")

    # Ativar/desativar o reconhecimento — detectado SEMPRE, antes de tudo.
    alvo = next((a for f, a in MIC_ORDENADO if f in texto_limpo), None)
    if alvo is None:
        alvo = casar_fuzzy(texto_limpo, MIC_FRASES, MIC_ORDENADO)

    # Desativado: o ÚNICO comando aceito é o de ATIVAR. Todo o resto — inclusive
    # encerrar e a confirmação — é ignorado enquanto o microfone está mudo.
    if not estado["ativo"]:
        if alvo is True:
            estado["ativo"] = True
            print("[SISTEMA] Microfone ativado")
        return

    # Ativo: aplica ativar/desativar e para por aqui.
    if alvo is not None:
        estado["ativo"] = alvo
        print(f"[SISTEMA] Microfone {'ativado' if alvo else 'desativado'}")
        return

    # Confirmação de encerramento pendente: só "confirmar" fecha; "cancelar"/"não" volta.
    if estado["encerrar"] == "confirmando":
        if "confirm" in texto_limpo or "pode encerrar" in texto_limpo:
            print("[SISTEMA] Encerramento confirmado por voz.")
            estado["encerrar"] = "encerrando"          # deixa a tela mostrar antes de fechar
            threading.Timer(0.8, encerrar_app).start()
        elif "cancel" in texto_limpo or "continuar" in texto_limpo or "nao" in texto_limpo:
            estado["encerrar"] = False
            print("[SISTEMA] Encerramento cancelado.")
        return  # ignora qualquer outra coisa enquanto aguarda confirmação

    # Comando único de ENCERRAR (sem fuzzy) → pede confirmação na tela.
    if any(p in texto_limpo for p in COMANDOS_ENCERRAR):
        estado["encerrar"] = "confirmando"
        print("[SISTEMA] Pedido de encerramento — aguardando confirmação na tela.")
        return

    # 1) Navegação entre enigmas — casamento exato (trecho da fala)
    for frase, acao in ENIGMA_ORDENADO:
        if frase in texto_limpo:
            estado["feedback"] = None; executar_acao_enigma(acao); return

    # 2) Placar de LEDs (Arduino) — casamento exato
    for frase, cmd in ARDUINO_ORDENADO:
        if frase in texto_limpo:
            enviar_arduino(cmd); return

    # 3) Fallback por SIMILARIDADE: se nada casou exato, aceita o comando mais
    #    parecido (tolera 'mostra a resposta', etc.).
    acao = casar_fuzzy(texto_limpo, ENIGMA_FRASES, ENIGMA_ORDENADO)
    if acao:
        estado["feedback"] = None; executar_acao_enigma(acao); return
    cmd = casar_fuzzy(texto_limpo, ARDUINO_FRASES, ARDUINO_ORDENADO)
    if cmd:
        enviar_arduino(cmd); return

    # 4) Resposta da pergunta só é avaliada na tela de pergunta
    if estado["tela"] != "pergunta": return

    letra = detectar_letra(texto_limpo)
    if letra is None: return  # fala não era um comando nem uma alternativa

    enigma = ENIGMAS[estado["enigma_index"]]
    if letra == enigma["correta"]:
        estado.update({"feedback": None, "tela": "resposta"})
        print(f"[ACERTO] Alternativa correta: '{letra.upper()}'")
    else:
        # Marca erro; o contador faz o HTML disparar a animação só uma vez por erro.
        estado["feedback"] = "erro"; estado["feedback_contador"] += 1
        print(f"[ERRO] Alternativa '{letra.upper()}' (correta: '{enigma['correta'].upper()}')")
        threading.Timer(8.0, limpar_feedback).start()  # limpa o feedback após 8s

def executar_acao_enigma(acao):
    # Aplica navegação/troca de tela. Ignora silenciosamente os limites (1º/último enigma).
    idx, total = estado["enigma_index"], estado["total"]
    if acao == "proximo" and idx < total - 1:
        estado.update({"enigma_index": idx + 1, "tela": "pergunta"})
        print(f"[SISTEMA] Avançou para o Enigma {estado['enigma_index'] + 1}")
    elif acao == "anterior" and idx > 0:
        estado.update({"enigma_index": idx - 1, "tela": "pergunta"})
        print(f"[SISTEMA] Voltou para o Enigma {estado['enigma_index'] + 1}")
    elif acao in ("resposta", "pergunta"):
        estado["tela"] = acao
        print(f"[SISTEMA] Tela atualizada: Mostrar {acao.capitalize()}")
    elif acao in ("menu_abrir", "menu_fechar"):
        estado["menu"] = (acao == "menu_abrir")
        print(f"[SISTEMA] Painel de comandos {'aberto' if estado['menu'] else 'fechado'}")


# ============================== FLASK (servidor da página) ==============================
app = Flask(__name__)

@app.route("/")
def index(): return send_file(os.path.join(DIRETORIO_ATUAL, "index.html"))

@app.route("/Estampa_Neuron.png")
def estampa():
    try: return send_file(os.path.join(DIRETORIO_ATUAL, "Estampa_Neuron.png"))
    except Exception: return "", 404

@app.route("/logo_neuron.png")
def logo():
    try: return send_file(os.path.join(DIRETORIO_ATUAL, "logo_neuron.png"))
    except Exception: return "", 404

@app.route("/estado")
def get_estado():
    #Snapshot do estado para o HTML (consultado a cada 300ms).
    e = ENIGMAS[estado["enigma_index"]]
    voz = "desativado" if (estado["voz"] == "ouvindo" and not estado["ativo"]) else estado["voz"]
    return jsonify({
        "enigma_index": estado["enigma_index"], "enigma_numero": estado["enigma_index"] + 1,
        "total": estado["total"], "tela": estado["tela"],
        "pergunta": e["pergunta"], "alternativas": e["alternativas"], "correta": e["correta"],
        "resposta": e["alternativas"][e["correta"]],  # texto da correta (tela de resposta)
        "feedback": estado["feedback"], "feedback_contador": estado["feedback_contador"],
        "menu": estado["menu"],  # painel de comandos aberto/fechado
        "voz": voz,              # carregando / ouvindo / desativado / erro
        "encerrar": estado["encerrar"],  # False / "confirmando" / "encerrando"
    })

# Rotas dos botões da página (espelham os comandos de voz).
@app.route("/proximo")
def proximo(): estado["feedback"] = None; executar_acao_enigma("proximo"); return "", 204
@app.route("/anterior")
def anterior(): estado["feedback"] = None; executar_acao_enigma("anterior"); return "", 204
@app.route("/resposta")
def mostrar_resposta(): estado["tela"] = "resposta"; return "", 204
@app.route("/pergunta")
def mostrar_pergunta(): estado["tela"] = "pergunta"; return "", 204
@app.route("/menu/<acao>")
def menu(acao): estado["menu"] = (acao == "abrir"); return "", 204

@app.route("/encerrar/<acao>")
def encerrar(acao):
    if acao == "confirmar":
        estado["encerrar"] = "encerrando"
        threading.Timer(0.8, encerrar_app).start()
    elif acao == "cancelar":
        estado["encerrar"] = False
    return "", 204

def iniciar_flask(): app.run(port=FLASK_PORT, debug=False, use_reloader=False)


# ============================== RECONHECIMENTO DE VOZ ==============================
def transcrever(model, audio_bytes):
    # Transcreve o áudio (int16) e devolve o texto, descartando ruído/alucinação.
    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        audio, language="pt", beam_size=5, # 5 = mais preciso (a GPU dá conta; no CPU use 1)
        vad_filter=True,                   # Silero VAD: trecho sem fala vira nada (corta ruído/silêncio)
        no_speech_threshold=0.6,           # prob. de silêncio acima disso = descarta o segmento
        condition_on_previous_text=False,  # cada fala é independente (não arrasta contexto)
        # SEM initial_prompt de comandos: era ele que o modelo "ecoava" no silêncio,
        # disparando comandos fantasmas (ex.: transcrever a lista inteira sem ninguém falar).
    )
    # Guarda extra: ignora segmentos que o próprio modelo marcou como provável silêncio.
    return " ".join(s.text for s in segments if s.no_speech_prob < 0.6).strip()

def iniciar_whisper():
    print("\n[Aguarde] Carregando o motor de voz (faster-whisper)...")
    try:
        model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=COMPUTE_TYPE)
    except Exception as e:
        estado["voz"] = "erro"
        print(f"[ERRO] Falha ao carregar o motor de voz: {e}")
        return
    print("[OK] Sistema de voz pronto. Escutando comandos...\n")

    fila_audio = queue.Queue()   # blocos crus vindos do microfone (preenchida no callback)
    fila_fala  = queue.Queue()   # falas já recortadas, prontas p/ transcrever

    def callback(indata, frames, time_info, status):
        fila_audio.put(bytes(indata))  # só enfileira; o trabalho pesado roda fora do callback

    def worker_transcricao():
        # Thread dedicada: transcreve e processa sem travar a captura de áudio.
        while True:
            audio = fila_fala.get()
            # Comando ao vivo: áudio antigo é inútil. Se a fila acumulou enquanto a
            # transcrição rodava, descarta tudo e fica só com a fala MAIS RECENTE — assim
            # frases velhas não saem com atraso conforme o jogo avança.
            descartadas = 0
            while not fila_fala.empty():
                audio = fila_fala.get_nowait(); descartadas += 1
            if descartadas:
                print(f"[VOZ] {descartadas} fala(s) atrasada(s) descartada(s).")
            texto = transcrever(model, audio)
            if texto: processar_texto(texto)
    threading.Thread(target=worker_transcricao, daemon=True).start()

    # Detecção de fala por volume, com pre-roll p/ não perder o começo das palavras.
    buffer_fala, blocos_silencio, falando = [], 0, False
    pre_roll = deque(maxlen=PRE_ROLL_BLOCOS)

    try:
        with sd.RawInputStream(samplerate=SAMPLERATE, blocksize=BLOCKSIZE, dtype="int16", channels=1, callback=callback):
            estado["voz"] = "ouvindo"  # microfone aberto: a página tira o aviso de status
            while True:
                bloco = fila_audio.get()
                if np.abs(np.frombuffer(bloco, dtype=np.int16)).mean() >= SILENCIO_LIMIAR:
                    # Está falando: ao iniciar, recupera os blocos do pre-roll.
                    if not falando: falando, buffer_fala = True, list(pre_roll)
                    buffer_fala.append(bloco); blocos_silencio = 0
                elif falando:
                    # Em silêncio durante uma fala: conta até confirmar o fim.
                    buffer_fala.append(bloco); blocos_silencio += 1
                    if blocos_silencio >= SILENCIO_BLOCOS:
                        if len(buffer_fala) >= MIN_BLOCOS_FALA:
                            fila_fala.put(b"".join(buffer_fala))  # entrega e já volta a escutar
                        buffer_fala, blocos_silencio, falando = [], 0, False
                else:
                    pre_roll.append(bloco)  # silêncio ocioso: mantém só os últimos blocos
    except Exception as e:
        estado["voz"] = "erro"
        print(f"[ERRO] Microfone indisponível: {e}")


# ============================== MAIN ==============================
if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=" * 55 + "\n  PLACAR NEURON — INICIADO\n" + "=" * 55)

    conectar_arduino()
    threading.Thread(target=iniciar_flask, daemon=True).start()
    time.sleep(1.5)  # dá um instante para o Flask subir antes de abrir o navegador
    webbrowser.open(f"http://localhost:{FLASK_PORT}")

    try:
        iniciar_whisper()
        # Se a voz falhou, iniciar_whisper retorna; mantém o processo vivo p/ o Flask
        # continuar servindo a página (que mostra o aviso de microfone indisponível).
        if estado["voz"] == "erro":
            print("[INFO] Reconhecimento de voz desativado. A página segue no ar (Ctrl+C para sair).")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SISTEMA] Encerrado pelo usuário. Até logo!")
    finally:
        if arduino and arduino.is_open: arduino.close()
