# Correduría GYA - Asistente de Voz

Servidor puente que conecta **Gemini Live API** + **Twilio** para llamadas de voz automáticas.

## Arquitectura

```
Persona llama → Twilio → WebSocket → Gemini Live API → Audio → Twilio → Persona
```

## Requisitos

- Python 3.10+
- Cuenta de Google Cloud con API key de Gemini
- Cuenta de Twilio con número de teléfono
- Servidor accesible desde internet (o ngrok para pruebas)

## Instalación

```bash
cd ~/correduria-voice
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tus credenciales
```

## Configurar .env

```bash
GEMINI_API_KEY=AIzaSy...           # Tu API key de Gemini
TWILIO_ACCOUNT_SID=AC...           # Account SID de Twilio
TWILIO_AUTH_TOKEN=...              # Auth Token de Twilio
TWILIO_PHONE_NUMBER=+34XXXXXXXXX   # Número comprado en Twilio
SERVER_HOST=tu-ip-o-dominio:8000   # Donde corre el servidor
```

## Ejecutar

```bash
cd ~/correduria-voice
python src/server.py
```

El servidor arranca en `http://localhost:8000`

## Para pruebas locales con ngrok

```bash
# Instalar ngrok
# Exponer el puerto 8000
ngrok http 8000

# Copiar la URL que te da (ej: https://abc123.ngrok.io)
# Configurar SERVER_HOST en .env con esa URL (sin https://)
```

## Configurar Twilio

1. En la consola de Twilio, ve a **Phone Numbers**
2. Selecciona tu número
3. En **A CALL COMES IN**, poner:
   - Webhook: `https://tu-dominio/incoming-call`
   - Method: POST

## Hacer llamadas de prueba

```bash
# Llamar a un número
curl -X POST http://localhost:8000/make-call \
  -H "Content-Type: application/json" \
  -d '{"to": "+34612345678"}'
```

## Flujo de una llamada

1. **Twilio recibe la llamada** → Llama al webhook `/incoming-call`
2. **Servidor responde con TwiML** → Conecta al WebSocket `/media-stream`
3. **Twilio envía audio** → Servidor convierte y reenvía a Gemini Live
4. **Gemini procesa** → Devuelve audio de respuesta
5. **Servidor convierte audio** → Lo envía de vuelta a Twilio → A la persona

## Formatos de audio

- **Twilio**: Mulaw 8kHz, base64
- **Gemini Live**: PCM16 16kHz
- El servidor convierte automáticamente entre formatos

## Costes estimados por llamada de 3 minutos

| Componente | Coste |
|-----------|-------|
| Gemini 2.5 Flash Live | ~0.03-0.05€ |
| Twilio (telefonía) | ~0.03€ |
| Número Twilio | ~1€/mes |
| **Total por llamada** | **~0.06-0.08€** |
