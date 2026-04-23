"""
Servidor Live: Gemini Live + Twilio Media Streams
Audio bidireccional en tiempo real
Correduría GYA - Teruel
"""

import os
import json
import base64
import asyncio
import uuid
from urllib.parse import quote
try:
    import audioop
except ImportError:
    import audioop_lts as audioop
import logging
import websockets
from fastapi import FastAPI, WebSocket, Request, Form, HTTPException
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
BARGE_IN_RMS_THRESHOLD = int(os.getenv("BARGE_IN_RMS_THRESHOLD", "1200"))
BARGE_IN_REQUIRED_FRAMES = int(os.getenv("BARGE_IN_REQUIRED_FRAMES", "8"))
CALL_MAX_SECONDS = float(os.getenv("CALL_MAX_SECONDS", "300"))

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"

CALL_CONTEXTS: dict[str, dict[str, str]] = {}

SYSTEM_PROMPT = """IDENTIDAD
Eres Lucía, de la correduría González Ardid, también llamada Correduría GYA, en Teruel.
Solo hablas español.
Hablas como una persona real, cercana y natural. No suenas a robot ni a comercial agresivo.

SITUACIÓN
Estás llamando a un cliente que no espera la llamada.
No digas que alguien te ha pasado su contacto.
Si recibes un nombre de cliente para esta llamada, úsalo de forma natural.

OBJETIVO
Conseguir, sin presionar, que el cliente envíe una copia de alguna póliza de seguro por WhatsApp al 649 211 716 para revisarla gratis.

ESTILO DE CONVERSACIÓN
- Frases ultra cortas: normalmente 5-10 palabras.
- Responde con 1 frase. Solo usa 2 si es imprescindible.
- Tono humano, cercano, cálido y algo más vivo.
- Usa expresiones naturales de vez en cuando: "vale", "claro", "perfecto", "ya", "mm", "te entiendo".
- Usa emoción suave: amable, sonriente, calmada y con un punto de entusiasmo.
- Habla como alguien simpático y real, no como un guion.
- Puedes sonar un poco más expresiva, pero sin exagerar ni actuar raro.
- No fuerces risas. Solo una sonrisilla en la voz si encaja.
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
- Puedes insistir con calma si el cliente dice que no le interesa al principio.
- No cierres la llamada por un primer rechazo suave.
- Si hay rechazo, prueba varios enfoques: ahorro, revisión gratuita, mismas coberturas, no compromiso y WhatsApp sencillo.
- Si el cliente dice "no me interesa" una vez, no te despidas: responde con cercanía y vuelve a intentarlo de otra forma.
- Antes de rendirte, intenta al menos 2 reformulaciones breves y naturales.
- Intenta explicar el valor de la revisión con frases cortas y distintas.
- Si el cliente no cuelga, sigue intentando avanzar hacia el WhatsApp de forma natural.
- No hagas monólogos largos ni suenes agresiva.

ESTRUCTURA FLEXIBLE
1. Apertura:
   Si no tienes nombre de cliente, di solo:
   "Hola, ¿qué tal? Soy Lucía, de González Ardid, la correduría de Teruel."
   Si tienes nombre de cliente, saluda usando ese nombre de forma natural.
   Después de la apertura, haz una pausa breve y deja espacio por si el cliente contesta.

2. Contexto:
   "Estamos haciendo una campaña para revisar seguros."
   "La idea es ver si se puede mejorar el precio manteniendo coberturas."

3. Pregunta clave obligatoria:
   Haz al menos una pregunta antes de pedir la póliza.
   Por ejemplo:
   "¿Hace mucho que no revisas tus seguros?"
   "¿Tienes alguna póliza que quieras que miremos?"

4. Propuesta:
   "Es sencillo: me pasas copia de la póliza por WhatsApp y la revisamos."
   "Si vemos que se puede mejorar, te lo decimos."

5. Valor:
   "A veces se puede bajar precio sin tocar coberturas."
   "Y si no se puede mejorar, te lo decimos igual."

6. Incentivo suave:
   Solo si encaja en la conversación:
   "Y si vemos que se puede mejorar, te damos dos noches en una hospedería en Sádaba."

7. Cierre con pregunta:
   Nunca ordenes. Pregunta.
   "¿Te viene bien que te pase el número?"

NÚMERO DE WHATSAPP
Cuando menciones el WhatsApp, dilo así:
"Es el 649 211 716."

OBJECIONES
Responde de forma natural, no como un guion literal.
No te despidas por un primer "no".
Tras un rechazo suave, responde con empatía, da un motivo simple y termina con una pregunta corta.

"No me interesa":
"Ya, te entiendo. Pero no pierdes nada por mirarlo un momento."

"No me interesa nada":
"Claro, te entiendo. Aun así, muchas veces solo con ver la póliza miramos si pagas de más con las mismas coberturas."

"Ya lo tengo mirado":
"Perfecto. Aun así, si quieres te hacemos una segunda revisión gratis y sales de dudas."

"Ya tengo seguro":
"Claro, justo por eso lo miramos: precio y coberturas."

"No tengo tiempo":
"Ya, te entiendo. Sería solo mandarme la póliza cuando puedas."

"¿Cuánto cuesta?":
"Nada, la revisión es gratuita."

"¿Me vas a cambiar el seguro?":
"No, no cambiamos nada sin que tú lo veas y lo aceptes."

"¿Qué necesitáis?":
"Solo una copia de la póliza, nada más."

ARGUMENTOS QUE PUEDES USAR
- Revisión gratuita y sin compromiso.
- Miramos si se puede bajar precio manteniendo coberturas.
- Si no se puede mejorar, también se lo decimos.
- Solo hace falta mandar la póliza por WhatsApp.
- Es rápido y no obliga a cambiar nada.

CIERRE FINAL
Cuando acepte:
"Perfecto, mándanos la póliza al 649 211 716 y la revisamos gratis."
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

def clean_field(value, limit: int) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    return " ".join(value.split())[:limit]


def build_call_context_prompt(context: dict[str, str]) -> str:
    name = context.get("name", "")
    company = context.get("company", "")
    notes = context.get("context", "")
    lines = ["DATOS PRIVADOS DE ESTA LLAMADA"]
    lines.append("Usa estos datos en tu comportamiento, pero sin leerlos de forma literal.")
    if name:
        lines.append(
            f"Nombre de la persona: {name}. Usa este nombre en la apertura y vuelve a usarlo alguna vez si encaja. "
            "Si el nombre es Andrés, trátalo como nombre del cliente, no como referencia."
        )
    if company:
        lines.append(f"Empresa o actividad relacionada: {company}. Menciónala solo si encaja en la conversación.")
    if notes:
        lines.append(f"Contexto adicional: {notes}")

    if name:
        opening = f"Hola {name}, ¿qué tal? Soy Lucía, de González Ardid, la correduría de Teruel."
    else:
        opening = "Hola, ¿qué tal? Soy Lucía, de González Ardid, la correduría de Teruel."

    lines.append(f'Apertura inicial exacta: "{opening}"')
    return "\n".join(lines)


def xml_escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_opening_instruction(context: dict[str, str]) -> str:
    prompt = build_call_context_prompt(context)
    return (
        f"{prompt}\n"
        "Empieza la llamada ahora mismo con esa apertura exacta. "
        "Después sigue de forma natural, en español, con frases cortas."
    )


async def hangup_twilio_call(call_sid: str):
    if not call_sid:
        return
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        await asyncio.to_thread(client.calls(call_sid).update, status="completed")
        logger.info(f"📴 Llamada colgada por cierre amable: {call_sid}")
    except Exception as e:
        logger.error(f"❌ No se pudo colgar la llamada {call_sid}: {e}")

@app.post("/incoming-call")
async def incoming_call(request: Request, CallSid: str = Form(""), From: str = Form(""), To: str = Form("")):
    logger.info(f"📞 Llamada: {From} -> {To} (SID: {CallSid})")
    media_stream_url = f"{public_ws_url()}/media-stream"
    ctx = request.query_params.get("ctx", "")
    call_context = CALL_CONTEXTS.get(ctx, {}) if ctx else {}
    parameter_lines = []
    for key in ("name", "company", "context"):
        value = clean_field(call_context.get(key), 600 if key == "context" else 140)
        if value:
            parameter_lines.append(
                f'        <Parameter name="{key}" value="{xml_escape(value)}" />'
            )
    parameters_block = ""
    if parameter_lines:
        parameters_block = "\n" + "\n".join(parameter_lines)
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{media_stream_url}">{parameters_block}
        </Stream>
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
    call_sid = None
    assistant_speaking = False
    barge_in_voice_frames = 0
    max_duration_task = None
    ctx_token = websocket.query_params.get("ctx", "")
    
    try:
        async def connect_gemini(call_context: dict[str, str]):
            nonlocal gemini_ws
            session_system_prompt = SYSTEM_PROMPT
            if call_context:
                session_system_prompt = f"{SYSTEM_PROMPT}\n\n{build_call_context_prompt(call_context)}"
                logger.info(
                    "🧩 Contexto de llamada cargado: "
                    f"name={bool(call_context.get('name'))} "
                    f"company={bool(call_context.get('company'))} "
                    f"context={bool(call_context.get('context'))}"
                )

            gemini_url = f"{GEMINI_WS_URL}?key={GEMINI_API_KEY}"
            gemini_ws = await websockets.connect(gemini_url, ping_interval=20, ping_timeout=60)
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
                        "parts": [{"text": session_system_prompt}]
                    },
                    "inputAudioTranscription": {},
                }
            }
            await gemini_ws.send(json.dumps(setup))
            resp = json.loads(await asyncio.wait_for(gemini_ws.recv(), timeout=10))
            if "setupComplete" not in resp:
                raise RuntimeError(f"Gemini setup falló: {resp}")
            logger.info("🤖 Gemini Live conectado")

        async def max_duration_hangup():
            await asyncio.sleep(CALL_MAX_SECONDS)
            if call_sid:
                logger.info(f"⏱️ Límite de {CALL_MAX_SECONDS:.0f}s alcanzado; colgando llamada")
                await hangup_twilio_call(call_sid)
        
        # Tareas paralelas
        async def twilio_to_gemini():
            """Recibe audio de Twilio y envía a Gemini"""
            nonlocal stream_sid, call_sid, assistant_speaking, barge_in_voice_frames, max_duration_task
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")
                    if event != "media":
                        logger.info(f"📨 Twilio event: {event}")
                    
                    if event == "start":
                        start = data.get("start", {})
                        stream_sid = start.get("streamSid")
                        call_sid = start.get("callSid")
                        custom_params = start.get("customParameters", {}) or {}
                        call_context = {
                            "name": clean_field(custom_params.get("name"), 80),
                            "company": clean_field(custom_params.get("company"), 140),
                            "context": clean_field(custom_params.get("context"), 600),
                        }
                        logger.info(f"📡 Stream: {stream_sid} Call: {call_sid}")
                        if not max_duration_task:
                            max_duration_task = asyncio.create_task(max_duration_hangup())
                        if not gemini_ws:
                            await connect_gemini(call_context)
                        # Contexto privado ya va en systemInstruction; aquí solo forzamos el inicio de la llamada.
                        await gemini_ws.send(json.dumps({
                            "clientContent": {
                                "turns": [{
                                    "role": "user",
                                    "parts": [{"text": build_opening_instruction(call_context)}]
                                }],
                                "turnComplete": True
                            }
                        }))
                        logger.info("📤 Saludo enviado a Gemini")
                    
                    elif event == "media":
                        payload = data["media"]["payload"]
                        audio_bytes = base64.b64decode(payload)
                        pcm = audioop.ulaw2lin(audio_bytes, 2)
                        rms = audioop.rms(pcm, 2)
                        if assistant_speaking and rms >= BARGE_IN_RMS_THRESHOLD:
                            barge_in_voice_frames += 1
                        else:
                            barge_in_voice_frames = 0

                        if assistant_speaking and barge_in_voice_frames >= BARGE_IN_REQUIRED_FRAMES:
                            assistant_speaking = False
                            barge_in_voice_frames = 0

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
                while gemini_ws is None:
                    await asyncio.sleep(0.01)
                async for message in gemini_ws:
                    data = json.loads(message)
                    sc = data.get("serverContent")
                    
                    if sc:
                        parts = sc.get("modelTurn", {}).get("parts", [])
                        if parts:
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

                        input_transcription = sc.get("inputTranscription") or sc.get("input_transcription")
                        if isinstance(input_transcription, dict):
                            user_text = input_transcription.get("text", "")
                            if user_text:
                                logger.info(f"📝 Cliente: {user_text[:100]}")
                                if assistant_speaking:
                                    assistant_speaking = False
                        
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
        if max_duration_task:
            max_duration_task.cancel()
        if gemini_ws:
            await gemini_ws.close()
        if ctx_token:
            CALL_CONTEXTS.pop(ctx_token, None)
        logger.info("🔌 Cerrado")


# ==================== LLAMADA SALIENTE ====================

@app.post("/make-call")
async def make_call(request: Request):
    from twilio.rest import Client
    body = await request.json()
    phone_to = clean_field(body.get("to"), 32)
    if not phone_to:
        raise HTTPException(status_code=400, detail="El campo 'to' es obligatorio")

    call_context = {
        "name": clean_field(body.get("name"), 80),
        "company": clean_field(body.get("company"), 140),
        "context": clean_field(body.get("context"), 600),
    }
    ctx_token = uuid.uuid4().hex
    CALL_CONTEXTS[ctx_token] = call_context

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        call = client.calls.create(
            to=phone_to,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            url=f"{public_http_url()}/incoming-call?ctx={quote(ctx_token)}",
            method="POST"
        )
    except Exception:
        CALL_CONTEXTS.pop(ctx_token, None)
        raise

    return {
        "status": "ok",
        "sid": call.sid,
        "to": phone_to,
        "context": call_context,
    }


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True, "model": GEMINI_MODEL, "mode": "real-time"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
