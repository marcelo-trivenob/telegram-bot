from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import json
import os
from typing import Dict, List

# ============================================
# CONFIGURACIÓN
# ============================================

TELEGRAM_TOKEN = "8514151421:AAHcPDLHAWGsZBIb2MG8UvpG_IFhsNZOZ2Q"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SHEET_ID = "1zCUs6ZbeoHTHOVSRa_njCkq9n3b9piI45Zjbb0EJ470"
SHEET_NAME = "Muestras Palta"

# Columnas (basadas en 0)
COL_REPORTE = 0      # A
COL_FECHA = 5        # F
COL_PRODUCTOR = 17   # R
COL_TIPO_ANALISIS = 21  # V

RANGO_DIAS = 3

# Estado conversacional en memoria
# Estructura: {user_id: {"estado": "esperando_confirmacion", "resultados": [...], "fecha": ..., "productor": ...}}
user_state = {}

app = FastAPI()

# ============================================
# INICIALIZAR GOOGLE SHEETS
# ============================================

def get_sheet():
    """Conecta a Google Sheets sin credenciales (acceso público si está compartido)"""
    try:
        # Intentar con credenciales en variable de entorno
        creds_json = os.getenv("GOOGLE_CREDS")
        if creds_json:
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_dict)
            client = gspread.authorize(creds)
        else:
            # Para acceso público, usar gspread sin auth
            client = gspread.Client()
        
        sheet = client.open_by_key(SHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)
        return worksheet
    except Exception as e:
        print(f"Error conectando a Sheets: {e}")
        return None

# ============================================
# FUNCIONES UTILITARIAS - FECHAS
# ============================================

def parsear_fecha(fecha_str):
    """Parsea fecha DD/MM/YYYY"""
    try:
        partes = fecha_str.split("/")
        dia = int(partes[0])
        mes = int(partes[1])
        anio = int(partes[2])
        return datetime(anio, mes, dia)
    except:
        return None

def formatear_fecha(date):
    """Formatea datetime a DD/MM/YYYY"""
    if isinstance(date, datetime):
        return date.strftime("%d/%m/%Y")
    return str(date)

def esta_en_rango_fecha(fecha_sheet, fecha_obj, dias=3):
    """Verifica si fecha está en rango ±días"""
    try:
        if isinstance(fecha_sheet, str):
            if "/" in fecha_sheet:
                fecha_sheet = parsear_fecha(fecha_sheet)
            else:
                return False
        
        if not isinstance(fecha_sheet, datetime):
            return False
        
        # Resetear horas
        fecha_sheet = fecha_sheet.replace(hour=0, minute=0, second=0, microsecond=0)
        fecha_obj = fecha_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        
        diferencia = abs((fecha_obj - fecha_sheet).days)
        return diferencia <= dias
    except:
        return False

# ============================================
# FUNCIONES UTILITARIAS - SIMILITUD
# ============================================

def distancia_levenshtein(a, b):
    """Calcula distancia de Levenshtein"""
    m = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    
    for i in range(len(a) + 1):
        m[i][0] = i
    for j in range(len(b) + 1):
        m[0][j] = j
    
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i-1] == b[j-1]:
                m[i][j] = m[i-1][j-1]
            else:
                m[i][j] = 1 + min(m[i-1][j], m[i][j-1], m[i-1][j-1])
    
    return m[len(a)][len(b)]

def es_similar(buscado, original, umbral=0.75):
    """Verifica similitud entre strings"""
    a = buscado.lower().strip()
    b = original.lower().strip()
    
    # Exacta
    if a == b:
        return True
    
    # Parcial
    if b.find(a) != -1 or a.find(b) != -1:
        return True
    
    # Levenshtein
    distancia = distancia_levenshtein(a, b)
    max_len = max(len(a), len(b))
    similitud = 1 - (distancia / max_len)
    
    return similitud >= umbral

# ============================================
# BÚSQUEDA EN SHEETS
# ============================================

def buscar_analisis(fecha_str, productor_str):
    """Busca análisis en el Sheet"""
    worksheet = get_sheet()
    if not worksheet:
        return None
    
    try:
        datos = worksheet.get_all_values()
        fecha_obj = parsear_fecha(fecha_str)
        
        if not fecha_obj:
            return None
        
        resultados = []
        
        # Iterar desde fila 2 (índice 1)
        for i in range(1, len(datos)):
            fila = datos[i]
            
            if len(fila) <= max(COL_FECHA, COL_PRODUCTOR, COL_TIPO_ANALISIS):
                continue
            
            fecha_sheet = fila[COL_FECHA]
            productor_sheet = fila[COL_PRODUCTOR]
            tipo_analisis = fila[COL_TIPO_ANALISIS] if len(fila) > COL_TIPO_ANALISIS else "N/A"
            reporte = fila[COL_REPORTE] if len(fila) > COL_REPORTE else ""
            
            if not fecha_sheet or not productor_sheet:
                continue
            
            # Validar fecha
            if not esta_en_rango_fecha(fecha_sheet, fecha_obj, RANGO_DIAS):
                continue
            
            # Validar productor
            if not es_similar(productor_str, productor_sheet):
                continue
            
            # Match encontrado
            resultados.append({
                "productor": productor_sheet.strip(),
                "fecha": formatear_fecha(fecha_sheet),
                "tipo_analisis": tipo_analisis.strip(),
                "reporte": reporte.strip(),
                "fila": i + 1
            })
        
        return resultados if resultados else None
    
    except Exception as e:
        print(f"Error en búsqueda: {e}")
        return None

# ============================================
# ENVIAR MENSAJES A TELEGRAM
# ============================================

async def send_message(chat_id, texto, reply_markup=None):
    """Envía mensaje a Telegram"""
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def crear_botones_confirmacion(productor):
    """Crea botones de confirmación"""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Sí", "callback_data": f"confirmar_si_{productor}"},
                {"text": "❌ No", "callback_data": f"confirmar_no"}
            ]
        ]
    }

def crear_botones_analisis(resultados):
    """Crea botones para seleccionar análisis"""
    botones = []
    
    for i, r in enumerate(resultados):
        botones.append([
            {
                "text": f"{i+1}️⃣ {r['tipo_analisis']}",
                "callback_data": f"analisis_{i}"
            }
        ])
    
    # Agregar botón "Todos"
    botones.append([
        {"text": "📦 Todos", "callback_data": "analisis_todos"}
    ])
    
    return {"inline_keyboard": botones}

# ============================================
# MANEJO DE MENSAJES
# ============================================

async def handle_text_message(chat_id, user_id, texto):
    """Maneja mensaje de texto"""
    
    # Parsear entrada: "fecha productor"
    partes = texto.split(maxsplit=1)
    
    if len(partes) < 2:
        await send_message(
            chat_id,
            "⚠️ Escribe: `fecha productor`\nEj: `21/11/2025 MARIO PAMPAS`"
        )
        return
    
    fecha_str = partes[0]
    productor_str = partes[1]
    
    # Validar fecha
    if not parsear_fecha(fecha_str):
        await send_message(
            chat_id,
            "❌ Fecha inválida.\nUsa: DD/MM/YYYY\nEj: `21/11/2025`"
        )
        return
    
    # Buscar
    resultados = buscar_analisis(fecha_str, productor_str)
    
    if not resultados:
        await send_message(
            chat_id,
            f"❌ Sin resultados para:\n📅 {fecha_str}\n👤 {productor_str}"
        )
        return
    
    # Guardar estado
    user_state[user_id] = {
        "estado": "esperando_confirmacion",
        "resultados": resultados,
        "fecha": fecha_str,
        "productor": resultados[0]["productor"],
        "index": 0
    }
    
    # Pedir confirmación del primer resultado
    estado = user_state[user_id]
    r = estado["resultados"][0]
    
    texto_confirmacion = f"¿Confirmas *{r['productor']}* del *{r['fecha']}*?"
    botones = crear_botones_confirmacion(r["productor"])
    
    await send_message(chat_id, texto_confirmacion, botones)

async def handle_callback(chat_id, user_id, callback_data):
    """Maneja callbacks de botones"""
    
    if user_id not in user_state:
        await send_message(chat_id, "❌ Sesión expirada. Intenta de nuevo.")
        return
    
    estado = user_state[user_id]
    
    # Confirmación
    if callback_data.startswith("confirmar_si"):
        estado["estado"] = "esperando_analisis"
        
        # Mostrar opciones de análisis
        resultados = estado["resultados"]
        
        texto = "🔍 *Análisis disponibles:*\n\n"
        for i, r in enumerate(resultados):
            texto += f"{i+1}️⃣ *{r['tipo_analisis']}*\n"
        
        texto += "\n¿Cuál(es) quieres?"
        botones = crear_botones_analisis(resultados)
        
        await send_message(chat_id, texto, botones)
    
    elif callback_data == "confirmar_no":
        # Mostrar siguiente resultado
        estado["index"] = estado.get("index", 0) + 1
        
        if estado["index"] >= len(estado["resultados"]):
            await send_message(chat_id, "❌ No hay más resultados.")
            del user_state[user_id]
            return
        
        # Pedir confirmación del siguiente
        r = estado["resultados"][estado["index"]]
        texto_confirmacion = f"¿Confirmas *{r['productor']}* del *{r['fecha']}*?"
        botones = crear_botones_confirmacion(r["productor"])
        
        await send_message(chat_id, texto_confirmacion, botones)
    
    # Seleccionar análisis
    elif callback_data.startswith("analisis_"):
        resultados = estado["resultados"]
        
        if callback_data == "analisis_todos":
            # Enviar todos
            texto = "📄 *Análisis seleccionados:*\n\n"
            
            for i, r in enumerate(resultados):
                texto += f"*{i+1}. {r['tipo_analisis']}* - {r['fecha']}\n"
                
                if r["reporte"]:
                    texto += f"[Ver PDF]({r['reporte']})\n"
                else:
                    texto += "PDF no disponible\n"
                
                texto += "\n"
            
            await send_message(chat_id, texto)
        
        else:
            # Enviar uno específico
            idx = int(callback_data.split("_")[1])
            r = resultados[idx]
            
            texto = f"*{r['tipo_analisis']}* - {r['fecha']}\n"
            if r["reporte"]:
                texto += f"[Ver PDF]({r['reporte']})"
            else:
                texto += "PDF no disponible"
            
            await send_message(chat_id, texto)
        
        # Limpiar estado
        del user_state[user_id]

# ============================================
# WEBHOOK
# ============================================

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        
        # Mensaje de texto
        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            
            if "text" in message:
                texto = message["text"].strip()
                
                if texto == "/start":
                    await send_message(
                        chat_id,
                        "🤖 *Bot de Análisis*\n\n"
                        "Escribe tu búsqueda:\n"
                        "`fecha productor`\n\n"
                        "Ej: `21/11/2025 MARIO PAMPAS`"
                    )
                else:
                    await handle_text_message(chat_id, user_id, texto)
        
        # Callback (botones)
        elif "callback_query" in data:
            callback = data["callback_query"]
            chat_id = callback["message"]["chat"]["id"]
            user_id = callback["from"]["id"]
            callback_data = callback["data"]
            
            await handle_callback(chat_id, user_id, callback_data)
    
    except Exception as e:
        print(f"Error en webhook: {e}")
    
    return JSONResponse({"ok": True})

# ============================================
# HEALTH CHECK
# ============================================

@app.get("/")
async def health():
    return {"status": "ok", "bot": "analysis_bot"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
