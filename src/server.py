"""
Servidor Live: Gemini 3.1 Flash Live Preview + Twilio Media Streams
Audio bidireccional en tiempo real
Correduría GYA - Teruel
"""

import os
import json
import base64
import asyncio
import audioop
import logging
import websockets
from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import Response
from dotenv import load_dotenv

load_dotenv()

import sys
logfile = open("/home/andres/correduria-voice/server.log", "a", buffering=1)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=[
    logging.StreamHandler(sys.stdout),
    logging.StreamHandler(logfile)
])
logger = logging.getLogger("correduria-voice")

app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "models/gemini-3.1-flash-live-preview"
SERVER_HOST = os.getenv("SERVER_HOST", "localhost:8000")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

SYSTEM_PROMPT = """Eres Lucía, de Correduría GYA en Teruel. SOLO HABLAS ESPAÑOL.

SITUACIÓN: Estás llamando a Pablo. Andrés Novella te ha pasado su número para ofrecerle un estudio gratuito de su seguro de hogar.

REGLAS:
- Respuestas CORTAS (1-2 frases máximo)
- Tono cercano, natural
- Usa fillers: "vale...", "claro", "perfecto"

QUÉ DECIR:
1. "Hola Pablo, te llamo desde Correduría GYA, de Teruel. Me ha pasado tu número Andrés."
2. "Hacemos estudios gratuitos de seguros de hogar, sin compromiso."
3. "Si mejoramos tu seguro, perfecto, y si no, no pasa nada."
4. "Para hacer el estudio solo necesito que me mandes tu póliza por WhatsApp al 649 211 716."
5. "Y si conseguimos mejorar tu seguro, te regalamos 2 noches en una Hospedería en Sádaba."

OBJECIONES:
"No me interesa" → "Vale, lo entiendo. Te dejo el número por si cambias de opinión."
"¿Cuánto cuesta?" → "Nada, es gratis. Solo si decides contratar algo nuevo."
"¿Quién es Andrés?" → "Andrés es de Correduría GYA, de Teruel. Le conoces a través de algún contacto."
"Ya tengo seguro" → "Claro, justamente por eso. Solo miro tu póliza, si no mejora, seguimos igual."

CIERRE (si acepta):
"Perfecto Pablo, mándame la póliza al WhatsApp 649 211 716 y lo estudiamos. ¡Un saludo!" """


# ==================== WEBHOOK TWILIO ====================

@app.post("/incoming-call")
async def incoming_call(CallSid: str = Form(""), From: str = Form(""), To: str = Form("")):
    logger.info(f"📞 Llamada: {From} -> {To} (SID: {CallSid})")
    
    ws_host = SERVER_HOST
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="es-ES">Un momento, te conecto.</Say>
    <Connect>
        <Stream url="wss://{ws_host}/media-stream" />
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
    
    try:
        # Conectar a Gemini Live API
        gemini_url = f"{GEMINI_WS_URL}?key={GEMINI_API_KEY}"
        gemini_ws = await websockets.connect(gemini_url, ping_interval=20, ping_timeout=60)
        
        # Setup
        setup = {
            "setup": {
                "model": GEMINI_MODEL,
                "generation_config": {
                    "response_modalities": ["AUDIO"]
                },
                "system_instruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                }
            }
        }
        await gemini_ws.send(json.dumps(setup))
        resp = json.loads(await asyncio.wait_for(gemini_ws.recv(), timeout=10))
        
        if "setupComplete" not in resp:
            logger.error(f"Gemini setup falló: {resp}")
            return
        
        logger.info("🤖 Gemini Live conectado")
        
        # Iniciar conversación con texto
        await gemini_ws.send(json.dumps({
            "realtime_input": {
                "text": "Hola, saluda al cliente y preséntate como Lucía de Correduría GYA de Teruel. Ofrecele un estudio gratuito de su seguro de hogar."
            }
        }))
        logger.info("📤 Saludo enviado a Gemini")
        
        # Tareas paralelas
        async def twilio_to_gemini():
            """Recibe audio de Twilio y envía a Gemini"""
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")
                    logger.info(f"📨 Twilio event: {event}")
                    
                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"📡 Stream: {stream_sid}")
                    
                    elif event == "media":
                        payload = data["media"]["payload"]
                        audio_bytes = base64.b64decode(payload)
                        pcm = audioop.ulaw2lin(audio_bytes, 2)
                        pcm = audioop.ratecv(pcm, 2, 1, 8000, 16000, None)[0]
                        b64_pcm = base64.b64encode(pcm).decode()
                        
                        await gemini_ws.send(json.dumps({
                            "realtime_input": {
                                "audio": {
                                    "data": b64_pcm,
                                    "mime_type": "audio/pcm;rate=16000"
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
            nonlocal stream_sid
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
                            
                            elif "text" in p:
                                logger.info(f"🤖 Dice: {p['text'][:100]}")
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
        url=f"https://{SERVER_HOST}/incoming-call",
        method="POST"
    )
    return {"status": "ok", "sid": call.sid}


@app.get("/")
async def health():
    return {"ok": True, "model": "gemini-3.1-flash-live-preview", "mode": "real-time"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
