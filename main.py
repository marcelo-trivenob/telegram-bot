from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import json
import os

# ============================================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
# ============================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
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
user_state = {}

app = FastAPI()

# ============================================
# CREDENCIALES GOOGLE
# ============================================

def get_credentials():
    try:
        creds_json = os.getenv("GOOGLE_CREDS")
        if not creds_json:
            print("❌ ERROR: GOOGLE_CREDS no está configurado")
            return None
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets.readonly'
            ]
        )
        return creds
    except Exception as e:
        print(f"❌ Error con credenciales: {e}")
        return None

# ============================================
# GOOGLE SHEETS - DATOS
# ============================================

def get_sheet():
    try:
        creds = get_credentials()
        if not creds:
            return None
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)
        print(f"✅ Conectado a Sheet: {SHEET_NAME}")
        return worksheet
    except Exception as e:
        print(f"❌ Error conectando a Sheets: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================
# GOOGLE SHEETS - HIPERVÍNCULOS
# ============================================

def get_hyperlink(fila_num):
    """Obtiene el hipervínculo de la columna A en una fila específica"""
    try:
        creds = get_credentials()
        if not creds:
            return None

        service = build('sheets', 'v4', credentials=creds)

        # Columna A = fila_num
        rango = f"'{SHEET_NAME}'!A{fila_num}"

        result = service.spreadsheets().get(
            spreadsheetId=SHEET_ID,
            ranges=[rango],
            fields="sheets/data/rowData/values/hyperlink"
        ).execute()

        sheets = result.get('sheets', [])
        if not sheets:
            return None

        rows = sheets[0].get('data', [{}])[0].get('rowData', [])
        if not rows:
            return None

        values = rows[0].get('values', [])
        if not values:
            return None

        link = values[0].get('hyperlink')
        print(f"✅ Link fila {fila_num}: {link}")
        return link

    except Exception as e:
        print(f"❌ Error obteniendo hyperlink fila {fila_num}: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================
# FUNCIONES - FECHAS
# ============================================

def parsear_fecha(fecha_str):
    try:
        partes = fecha_str.split("/")
        return datetime(int(partes[2]), int(partes[1]), int(partes[0]))
    except:
        return None

def formatear_fecha(date):
    if isinstance(date, datetime):
        return date.strftime("%d/%m/%Y")
    return str(date)

def esta_en_rango_fecha(fecha_sheet, fecha_obj, dias=3):
    try:
        if isinstance(fecha_sheet, str):
            if "/" in fecha_sheet:
                fecha_sheet = parsear_fecha(fecha_sheet)
            else:
                return False
        if not isinstance(fecha_sheet, datetime):
            return False
        fecha_sheet = fecha_sheet.replace(hour=0, minute=0, second=0, microsecond=0)
        fecha_obj = fecha_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        diferencia = abs((fecha_obj - fecha_sheet).days)
        return diferencia <= dias
    except:
        return False

# ============================================
# FUNCIONES - SIMILITUD
# ============================================

def distancia_levenshtein(a, b):
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
    a = buscado.lower().strip()
    b = original.lower().strip()
    if a == b: return True
    if b.find(a) != -1 or a.find(b) != -1: return True
    distancia = distancia_levenshtein(a, b)
    max_len = max(len(a), len(b))
    similitud = 1 - (distancia / max_len)
    return similitud >= umbral

# ============================================
# BÚSQUEDA EN SHEETS
# ============================================

def buscar_analisis(fecha_str, productor_str):
    worksheet = get_sheet()
    if not worksheet:
        return None

    try:
        datos = worksheet.get_all_values()
        fecha_obj = parsear_fecha(fecha_str)
        if not fecha_obj:
            return None

        resultados = []

        for i in range(1, len(datos)):
            fila = datos[i]

            if len(fila) <= max(COL_FECHA, COL_PRODUCTOR):
                continue

            fecha_sheet = fila[COL_FECHA]
            productor_sheet = fila[COL_PRODUCTOR]
            tipo_analisis = fila[COL_TIPO_ANALISIS] if len(fila) > COL_TIPO_ANALISIS else "N/A"

            if not fecha_sheet or not productor_sheet:
                continue
            if not esta_en_rango_fecha(fecha_sheet, fecha_obj, RANGO_DIAS):
                continue
            if not es_similar(productor_str, productor_sheet):
                continue

            resultados.append({
                "productor": productor_sheet.strip(),
                "fecha": fecha_sheet if isinstance(fecha_sheet, str) else formatear_fecha(fecha_sheet),
                "tipo_analisis": tipo_analisis.strip() if tipo_analisis else "N/A",
                "fila": i + 1  # Guardamos número de fila para obtener link después
            })

        return resultados if resultados else None

    except Exception as e:
        print(f"❌ Error en búsqueda: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================
# TELEGRAM - ENVIAR MENSAJES
# ============================================

async def send_message(chat_id, texto, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
        print(f"Telegram response: {r.status_code}")

async def answer_callback(callback_query_id):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id}
        )

# ============================================
# TELEGRAM - BOTONES
# ============================================

def botones_confirmacion():
    return {
        "inline_keyboard": [[
            {"text": "✅ Sí", "callback_data": "confirmar_si"},
            {"text": "❌ No, otro", "callback_data": "confirmar_no"}
        ]]
    }

def botones_analisis(resultados):
    botones = []
    for i, r in enumerate(resultados):
        tipo = r['tipo_analisis'] or "N/A"
        botones.append([{
            "text": f"{i+1}️⃣ {tipo}",
            "callback_data": f"analisis_{i}"
        }])
    botones.append([{"text": "📦 Todos", "callback_data": "analisis_todos"}])
    return {"inline_keyboard": botones}

# ============================================
# MANEJO DE MENSAJES
# ============================================

async def handle_text(chat_id, user_id, texto):
    partes = texto.split(maxsplit=1)

    if len(partes) < 2:
        await send_message(
            chat_id,
            "⚠️ Escribe: `fecha productor`\nEj: `21/11/2025 MARIO PAMPAS`"
        )
        return

    fecha_str = partes[0]
    productor_str = partes[1]

    if not parsear_fecha(fecha_str):
        await send_message(chat_id, "❌ Fecha inválida.\nUsa: DD/MM/YYYY\nEj: `21/11/2025`")
        return

    await send_message(chat_id, "🔍 Buscando...")

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
        "index": 0
    }

    # Mostrar primer resultado para confirmar
    r = resultados[0]
    await send_message(
        chat_id,
        f"¿Es *{r['productor']}* del *{r['fecha']}*?",
        botones_confirmacion()
    )

async def enviar_resultado(chat_id, r):
    """Envía un resultado con su PDF"""
    link = get_hyperlink(r["fila"])

    texto = f"🔬 *{r['tipo_analisis']}*\n"
    texto += f"👤 {r['productor']}\n"
    texto += f"📅 {r['fecha']}\n"

    if link:
        texto += f"📄 [Ver PDF]({link})"
    else:
        texto += "📄 PDF no disponible"

    await send_message(chat_id, texto)

async def handle_callback(chat_id, user_id, callback_data, callback_query_id):
    await answer_callback(callback_query_id)

    if user_id not in user_state:
        await send_message(chat_id, "❌ Sesión expirada. Busca de nuevo.")
        return

    estado = user_state[user_id]

    # Confirmación SI
    if callback_data == "confirmar_si":
        estado["estado"] = "esperando_analisis"
        resultados = estado["resultados"]

        texto = "🔬 *Análisis disponibles:*\n\n"
        for i, r in enumerate(resultados):
            texto += f"{i+1}️⃣ *{r['tipo_analisis']}*\n"
        texto += "\n¿Cuál(es) quieres?"

        await send_message(chat_id, texto, botones_analisis(resultados))

    # Confirmación NO
    elif callback_data == "confirmar_no":
        estado["index"] = estado.get("index", 0) + 1

        if estado["index"] >= len(estado["resultados"]):
            await send_message(chat_id, "❌ No hay más resultados. Intenta otra búsqueda.")
            del user_state[user_id]
            return

        r = estado["resultados"][estado["index"]]
        await send_message(
            chat_id,
            f"¿Es *{r['productor']}* del *{r['fecha']}*?",
            botones_confirmacion()
        )

    # Seleccionar análisis - TODOS
    elif callback_data == "analisis_todos":
        resultados = estado["resultados"]
        await send_message(chat_id, f"📦 Enviando {len(resultados)} análisis...")

        for r in resultados:
            await enviar_resultado(chat_id, r)

        del user_state[user_id]

    # Seleccionar análisis - UNO
    elif callback_data.startswith("analisis_"):
        idx = int(callback_data.split("_")[1])
        r = estado["resultados"][idx]

        await enviar_resultado(chat_id, r)
        del user_state[user_id]

# ============================================
# WEBHOOK
# ============================================

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.body()
        print(f"📩 Webhook recibido")

        data = json.loads(body)

        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]

            if "text" in message:
                texto = message["text"].strip()
                print(f"Mensaje de {user_id}: {texto}")

                if texto == "/start":
                    await send_message(
                        chat_id,
                        "🤖 *Bot de Análisis de Palta*\n\n"
                        "Escribe tu búsqueda:\n"
                        "`fecha productor`\n\n"
                        "Ej: `21/11/2025 MARIO PAMPAS`"
                    )
                else:
                    await handle_text(chat_id, user_id, texto)

        elif "callback_query" in data:
            callback = data["callback_query"]
            chat_id = callback["message"]["chat"]["id"]
            user_id = callback["from"]["id"]
            callback_data = callback["data"]
            callback_query_id = callback["id"]

            print(f"Callback de {user_id}: {callback_data}")
            await handle_callback(chat_id, user_id, callback_data, callback_query_id)

    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        import traceback
        traceback.print_exc()

    return JSONResponse({"ok": True})

@app.head("/webhook")
async def webhook_head():
    return JSONResponse({"ok": True})

@app.get("/")
async def health():
    return {"status": "ok", "bot": "analysis_bot"}

@app.post("/")
async def root_post():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
