"""
Manejador de Gemini Live API
Conexión WebSocket para audio bidireccional
"""

import json
import base64
import asyncio
import logging
from typing import AsyncGenerator, Optional

import websockets

logger = logging.getLogger("gemini-live")

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


class GeminiLiveSession:
    """Sesión de Gemini Live API para conversaciones de voz en tiempo real"""
    
    def __init__(self, api_key: str, model: str = "models/gemini-2.5-flash-native-audio-latest"):
        self.api_key = api_key
        self.model = model
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None
    
    async def connect(self, system_prompt: str = ""):
        """Conectar a Gemini Live API"""
        url = f"{GEMINI_WS_URL}?key={self.api_key}"
        
        self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=60)
        
        # Setup - configurar idioma español y voz
        setup = {
            "setup": {
                "model": self.model,
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "language_code": "es-ES"
                    }
                },
                "system_instruction": {
                    "parts": [{"text": system_prompt}]
                }
            }
        }
        
        await self.ws.send(json.dumps(setup))
        
        # Esperar setup complete
        response = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
        
        if "setupComplete" in response:
            logger.info("✅ Gemini Live conectado")
        else:
            raise Exception(f"Setup falló: {response}")
        
        # Iniciar tarea de recepción
        self._receive_task = asyncio.create_task(self._receive_loop())
    
    async def send_audio(self, audio_pcm: bytes):
        """Enviar audio PCM16 16kHz a Gemini"""
        if not self.ws:
            return
        
        b64_audio = base64.b64encode(audio_pcm).decode()
        
        msg = {
            "realtime_input": {
                "media_chunks": [{
                    "data": b64_audio,
                    "mime_type": "audio/pcm;rate=16000"
                }]
            }
        }
        
        await self.ws.send(json.dumps(msg))
    
    async def finish_turn(self):
        """Indicar que el turno del usuario ha terminado"""
        if not self.ws:
            return
        
        msg = {
            "client_content": {
                "turn_complete": True
            }
        }
        
        await self.ws.send(json.dumps(msg))
        logger.info("📤 Turno completado")
    
    async def receive_audio(self) -> AsyncGenerator[bytes, None]:
        """Generador que produce chunks de audio de Gemini"""
        while True:
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                yield chunk
            except asyncio.TimeoutError:
                if self.ws and self.ws.closed:
                    break
                continue
    
    async def _receive_loop(self):
        """Bucle de recepción de mensajes de Gemini"""
        try:
            async for message in self.ws:
                data = json.loads(message)
                
                sc = data.get("serverContent")
                if sc:
                    # Extraer audio de la respuesta
                    model_turn = sc.get("modelTurn", {})
                    parts = model_turn.get("parts", [])
                    
                    for part in parts:
                        if "inlineData" in part:
                            b64_data = part["inlineData"].get("data", "")
                            if b64_data:
                                audio_bytes = base64.b64decode(b64_data)
                                await self._audio_queue.put(audio_bytes)
                    
                    # Log de texto si viene
                    for part in parts:
                        if "text" in part:
                            logger.info(f"🤖 Gemini: {part['text'][:100]}")
                
                # Metadata de uso
                if "usageMetadata" in data:
                    usage = data["usageMetadata"]
                    logger.debug(f"📊 Tokens: {usage}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.info("🔌 Conexión Gemini cerrada")
        except Exception as e:
            logger.error(f"❌ Error en receive loop: {e}")
    
    async def close(self):
        """Cerrar la sesión"""
        if self._receive_task:
            self._receive_task.cancel()
        if self.ws:
            await self.ws.close()
        logger.info("🔌 Sesión Gemini cerrada")
