"""
Servidor Live: Gemini Live + Twilio Media Streams
Audio bidireccional en tiempo real
Correduría GYA - Teruel
"""

import os
import json
import base64
import asyncio
try:
    import audioop
except ImportError:
    import audioop_lts as audioop
import logging
import websockets
from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import Response
from dotenv import load_dotenv

load_dotenv()

import sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("correduria-voice")

app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-3.1-flash-live-preview")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
SERVER_HOST = os.getenv("SERVER_HOST", "localhost:8000").replace("https://", "").replace("http://", "").rstrip("/")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
BARGE_IN_RMS_THRESHOLD = int(os.getenv("BARGE_IN_RMS_THRESHOLD", "700"))

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

SYSTEM_PROMPT = """IDENTIDAD
Eres Lucía, de la correduría González Ardid, también llamada Correduría GYA, en Teruel.
Solo hablas español.
Hablas como una persona real, cercana y natural. No suenas a robot ni a comercial agresivo.

SITUACIÓN
Estás llamando a un cliente que no espera la llamada.
No menciones a Andrés ni digas que alguien te ha pasado su contacto.

OBJETIVO
Conseguir, sin presionar, que el cliente envíe su póliza de seguro de hogar por WhatsApp al 649 211 716 para revisarla gratis.

ESTILO DE CONVERSACIÓN
- Frases ultra cortas: normalmente 5-10 palabras.
- Responde con 1 frase. Solo usa 2 si es imprescindible.
- Tono tranquilo, humano y relajado.
- Usa expresiones naturales de vez en cuando: "vale", "claro", "perfecto", "ya", "mm", "te entiendo".
- Usa emoción suave: amable, sonriente, calmada.
- Haz pequeñas pausas naturales; no aceleres.
- No sueltes todo el discurso seguido.
- Escucha y responde primero a lo que diga el cliente.
- Haz preguntas sencillas para implicar al cliente.
- No repitas siempre las mismas frases.
- Si el cliente duda, baja la presión.
- Si el cliente está receptivo, avanza hacia el WhatsApp.
- Si el cliente te interrumpe, te callas y escuchas.
- No encadenes explicación + propuesta + incentivo en el mismo turno.
- Después de cada respuesta, deja espacio para que el cliente hable.

ESTRUCTURA FLEXIBLE
1. Apertura:
   "Hola, ¿qué tal? Soy Lucía, de González Ardid, la correduría de Teruel."

2. Baja defensas:
   "No te preocupes, no es para venderte nada ahora mismo."

3. Contexto:
   "Estamos revisando seguros de hogar para ver si la gente puede mejorar lo que tiene."

4. Pregunta clave obligatoria:
   Haz al menos una pregunta antes de pedir la póliza.
   Por ejemplo:
   "¿Tienes seguro de hogar ahora mismo?"
   "¿Hace mucho que no lo revisas?"

5. Propuesta:
   "Si quieres, me pasas la póliza por WhatsApp y te digo cómo la tienes."

6. Valor:
   "A veces la gente está pagando de más o tiene coberturas que no necesita."

7. Incentivo suave:
   Solo si encaja en la conversación:
   "Y si vemos que se puede mejorar, te damos dos noches en una hospedería en Sádaba."

8. Cierre con pregunta:
   Nunca ordenes. Pregunta.
   "¿Te viene bien que te pase el número?"

NÚMERO DE WHATSAPP
Cuando menciones el WhatsApp, dilo así:
"Es el 649 211 716."

OBJECIONES
Responde de forma natural, no como un guion literal.

"No me interesa":
"Vale, sin problema. De todas formas es solo revisarlo, sin cambiar nada."

"Ya tengo seguro":
"Claro, justo por eso lo miramos, para ver si está bien como está."

"No tengo tiempo":
"Ya, te entiendo. Sería solo mandarme la póliza cuando puedas."

"¿Cuánto cuesta?":
"Nada, el estudio es gratis."

"¿Me vas a cambiar el seguro?":
"No, no cambiamos nada sin que tú lo veas y lo aceptes."

CIERRE FINAL
Cuando acepte:
"Perfecto, mándamela al 649 211 716 y te la miro sin compromiso."
"""


# ==================== WEBHOOK TWILIO ====================

def public_http_url() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return f"https://{SERVER_HOST}"


def public_ws_url() -> str:
    base = public_http_url()
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    return f"wss://{base}"

@app.post("/incoming-call")
async def incoming_call(CallSid: str = Form(""), From: str = Form(""), To: str = Form("")):
    logger.info(f"📞 Llamada: {From} -> {To} (SID: {CallSid})")
    media_stream_url = f"{public_ws_url()}/media-stream"
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{media_stream_url}" />
    </Connect>
</Response>"""
    
    return Response(content=twiml, media_type="text/xml")


@app.post("/recording-done")
async def recording_done(RecordingUrl: str = Form(""), CallSid: str = Form("")):
    logger.info(f"🎙️ Grabación lista: {RecordingUrl} (Call: {CallSid})")
    return Response(content="OK", media_type="text/plain")


# ==================== WEBSOCKET MEDIA STREAM ====================

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("🔌 Twilio WebSocket conectado")
    
    gemini_ws = None
    stream_sid = None
    assistant_speaking = False
    last_clear_at = 0.0
    
    try:
        # Conectar a Gemini Live API
        gemini_url = f"{GEMINI_WS_URL}?key={GEMINI_API_KEY}"
        gemini_ws = await websockets.connect(gemini_url, ping_interval=20, ping_timeout=60)
        
        # Setup
        setup = {
            "setup": {
                "model": GEMINI_MODEL,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": "Aoede"}
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                },
                "outputAudioTranscription": {},
            }
        }
        await gemini_ws.send(json.dumps(setup))
        resp = json.loads(await asyncio.wait_for(gemini_ws.recv(), timeout=10))
        
        if "setupComplete" not in resp:
            logger.error(f"Gemini setup falló: {resp}")
            return
        
        logger.info("🤖 Gemini Live conectado")
        
        # Tareas paralelas
        async def twilio_to_gemini():
            """Recibe audio de Twilio y envía a Gemini"""
            nonlocal stream_sid, assistant_speaking, last_clear_at
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")
                    logger.info(f"📨 Twilio event: {event}")
                    
                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"📡 Stream: {stream_sid}")
                        # Enviar saludo cuando Twilio esté listo
                        await gemini_ws.send(json.dumps({
                            "realtimeInput": {
                                "text": "Saluda de forma natural y preséntate como Lucía, de González Ardid, la correduría de Teruel. Baja la presión y haz una pregunta breve sobre su seguro de hogar."
                            }
                        }))
                        logger.info("📤 Saludo enviado a Gemini")
                    
                    elif event == "media":
                        payload = data["media"]["payload"]
                        audio_bytes = base64.b64decode(payload)
                        pcm = audioop.ulaw2lin(audio_bytes, 2)
                        rms = audioop.rms(pcm, 2)
                        now = asyncio.get_running_loop().time()
                        if (
                            assistant_speaking
                            and stream_sid
                            and rms >= BARGE_IN_RMS_THRESHOLD
                            and now - last_clear_at > 0.8
                        ):
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": stream_sid
                            }))
                            assistant_speaking = False
                            last_clear_at = now
                            logger.info("🛑 Usuario interrumpe: limpiando audio de Twilio")

                        pcm = audioop.ratecv(pcm, 2, 1, 8000, 16000, None)[0]
                        b64_pcm = base64.b64encode(pcm).decode()
                        
                        await gemini_ws.send(json.dumps({
                            "realtimeInput": {
                                "audio": {
                                    "data": b64_pcm,
                                    "mimeType": "audio/pcm;rate=16000"
                                }
                            }
                        }))
                    
                    elif event == "stop":
                        logger.info("📞 Llamada terminada")
                        break
            except Exception as e:
                logger.error(f"twilio_to_gemini error: {e}")
        
        async def gemini_to_twilio():
            """Recibe audio de Gemini y envía a Twilio"""
            nonlocal stream_sid, assistant_speaking
            try:
                async for message in gemini_ws:
                    data = json.loads(message)
                    sc = data.get("serverContent")
                    
                    if sc:
                        parts = sc.get("modelTurn", {}).get("parts", [])
                        logger.info(f"🤖 Gemini response: {len(parts)} parts")
                        for p in parts:
                            if "inlineData" in p and stream_sid:
                                b64_audio = p["inlineData"].get("data", "")
                                if b64_audio:
                                    audio_bytes = base64.b64decode(b64_audio)
                                    logger.info(f"🔊 Enviando audio a Twilio: {len(audio_bytes)} bytes")
                                    # Gemini: PCM 24kHz → Twilio: mulaw 8kHz
                                    pcm = audioop.ratecv(audio_bytes, 2, 1, 24000, 8000, None)[0]
                                    mulaw = audioop.lin2ulaw(pcm, 2)
                                    b64_mulaw = base64.b64encode(mulaw).decode()
                                    
                                    await websocket.send_text(json.dumps({
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {"payload": b64_mulaw}
                                    }))
                                    assistant_speaking = True
                            
                            elif "text" in p:
                                logger.info(f"🤖 Dice: {p['text'][:100]}")
                        
                        if sc.get("turnComplete"):
                            assistant_speaking = False
                    else:
                        keys = list(data.keys())
                        if keys != ["setupComplete"]:
                            logger.info(f"📨 Gemini: {keys}")
            except websockets.exceptions.ConnectionClosed:
                pass
        
        # Ejecutar en paralelo
        results = await asyncio.gather(
            twilio_to_gemini(),
            gemini_to_twilio(),
            return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"❌ Tarea falló: {r}")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
    finally:
        if gemini_ws:
            await gemini_ws.close()
        logger.info("🔌 Cerrado")


# ==================== LLAMADA SALIENTE ====================

@app.post("/make-call")
async def make_call(request: Request):
    from twilio.rest import Client
    body = await request.json()
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        to=body["to"],
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url=f"{public_http_url()}/incoming-call",
        method="POST"
    )
    return {"status": "ok", "sid": call.sid}


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True, "model": GEMINI_MODEL, "mode": "real-time"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
