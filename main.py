from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import json
import os

# ============================================
# CONFIGURACIÓN
# ============================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SHEET_ID = "1zCUs6ZbeoHTHOVSRa_njCkq9n3b9piI45Zjbb0EJ470"
SHEET_NAME = "Muestras Palta"

COL_REPORTE = 0
COL_FECHA = 5
COL_PRODUCTOR = 17
COL_TIPO_ANALISIS = 21

RANGO_DIAS = 3
SESSION_TIMEOUT_MIN = 10

SALUDOS = [
    "hola", "hi", "buenos dias", "buenas tardes", "buenas noches",
    "buenas", "inicio", "iniciar", "start", "/start", "ola",
    "buen dia", "hey", "good morning", "empezar", "comenzar"
]

user_state = {}

app = FastAPI()

# ============================================
# CREDENCIALES GOOGLE
# ============================================

def get_credentials():
    try:
        creds_json = os.getenv("GOOGLE_CREDS")
        if not creds_json:
            print("❌ ERROR: GOOGLE_CREDS no configurado")
            return None
        creds_dict = json.loads(creds_json)
        return Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
    except Exception as e:
        print(f"❌ Error credenciales: {e}")
        return None

# ============================================
# GOOGLE SHEETS - DATOS
# ============================================

def get_all_data():
    try:
        creds = get_credentials()
        if not creds:
            return None
        client = gspread.authorize(creds)
        worksheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
        print("✅ Conectado a Sheet")
        return worksheet.get_all_values()
    except Exception as e:
        print(f"❌ Error Sheet: {e}")
        return None

def get_hyperlink(fila_num):
    try:
        creds = get_credentials()
        if not creds:
            return None
        service = build('sheets', 'v4', credentials=creds)
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
        print(f"❌ Error hyperlink fila {fila_num}: {e}")
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
            fecha_sheet = parsear_fecha(fecha_sheet) if "/" in fecha_sheet else None
        if not isinstance(fecha_sheet, datetime):
            return False
        fecha_sheet = fecha_sheet.replace(hour=0, minute=0, second=0, microsecond=0)
        fecha_obj = fecha_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        return abs((fecha_obj - fecha_sheet).days) <= dias
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
    similitud = 1 - (distancia / max(len(a), len(b)))
    return similitud >= umbral

# ============================================
# BÚSQUEDA
# ============================================

def buscar_por_nombre(nombre_str):
    """Busca productores similares y retorna lista única de nombres"""
    datos = get_all_data()
    if not datos:
        return None

    nombres_encontrados = {}

    for i in range(1, len(datos)):
        fila = datos[i]
        if len(fila) <= COL_PRODUCTOR:
            continue
        productor = fila[COL_PRODUCTOR].strip()
        if not productor:
            continue
        if es_similar(nombre_str, productor):
            if productor not in nombres_encontrados:
                nombres_encontrados[productor] = []
            nombres_encontrados[productor].append(i + 1)

    return nombres_encontrados if nombres_encontrados else None

def buscar_fechas_por_productor(productor_exacto):
    """Retorna todas las fechas disponibles para un productor"""
    datos = get_all_data()
    if not datos:
        return None

    fechas = {}

    for i in range(1, len(datos)):
        fila = datos[i]
        if len(fila) <= max(COL_FECHA, COL_PRODUCTOR):
            continue
        productor = fila[COL_PRODUCTOR].strip()
        fecha = fila[COL_FECHA].strip() if fila[COL_FECHA] else ""
        if productor == productor_exacto and fecha:
            if fecha not in fechas:
                fechas[fecha] = []
            fechas[fecha].append(i + 1)

    return fechas if fechas else None

def buscar_analisis_por_productor_fecha(productor_exacto, fecha_str):
    """Retorna análisis de un productor en una fecha"""
    datos = get_all_data()
    if not datos:
        return None

    fecha_obj = parsear_fecha(fecha_str)
    if not fecha_obj:
        return None

    resultados = []

    for i in range(1, len(datos)):
        fila = datos[i]
        if len(fila) <= max(COL_FECHA, COL_PRODUCTOR):
            continue
        productor = fila[COL_PRODUCTOR].strip()
        fecha_sheet = fila[COL_FECHA].strip() if fila[COL_FECHA] else ""
        tipo = fila[COL_TIPO_ANALISIS].strip() if len(fila) > COL_TIPO_ANALISIS else "N/A"

        if productor != productor_exacto:
            continue
        if not esta_en_rango_fecha(fecha_sheet, fecha_obj, RANGO_DIAS):
            continue

        resultados.append({
            "productor": productor,
            "fecha": fecha_sheet,
            "tipo_analisis": tipo or "N/A",
            "fila": i + 1
        })

    return resultados if resultados else None

def buscar_por_fecha_y_nombre(fecha_str, nombre_str):
    """Búsqueda combinada fecha + nombre"""
    datos = get_all_data()
    if not datos:
        return None

    fecha_obj = parsear_fecha(fecha_str)
    if not fecha_obj:
        return None

    resultados = []

    for i in range(1, len(datos)):
        fila = datos[i]
        if len(fila) <= max(COL_FECHA, COL_PRODUCTOR):
            continue
        fecha_sheet = fila[COL_FECHA]
        productor = fila[COL_PRODUCTOR].strip()
        tipo = fila[COL_TIPO_ANALISIS].strip() if len(fila) > COL_TIPO_ANALISIS else "N/A"

        if not fecha_sheet or not productor:
            continue
        if not esta_en_rango_fecha(fecha_sheet, fecha_obj, RANGO_DIAS):
            continue
        if not es_similar(nombre_str, productor):
            continue

        resultados.append({
            "productor": productor,
            "fecha": fecha_sheet if isinstance(fecha_sheet, str) else formatear_fecha(fecha_sheet),
            "tipo_analisis": tipo or "N/A",
            "fila": i + 1
        })

    return resultados if resultados else None

# ============================================
# SESIONES - TIMEOUT
# ============================================

def session_expirada(user_id):
    if user_id not in user_state:
        return True
    estado = user_state[user_id]
    ultima = estado.get("ultima_actividad")
    if not ultima:
        return True
    return datetime.now() - ultima > timedelta(minutes=SESSION_TIMEOUT_MIN)

def actualizar_sesion(user_id):
    if user_id in user_state:
        user_state[user_id]["ultima_actividad"] = datetime.now()

def limpiar_sesion(user_id):
    if user_id in user_state:
        del user_state[user_id]

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
        print(f"Telegram: {r.status_code}")

async def answer_callback(callback_query_id):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id}
        )

# ============================================
# BOTONES
# ============================================

def botones_inicio():
    return {
        "inline_keyboard": [[
            {"text": "📅 Buscar por fecha y nombre", "callback_data": "modo_fecha"},
            {"text": "👤 Buscar por nombre", "callback_data": "modo_nombre"}
        ]]
    }

def botones_cancelar():
    return {
        "inline_keyboard": [[
            {"text": "❌ Cancelar", "callback_data": "cancelar"}
        ]]
    }

def botones_nueva_busqueda():
    return {
        "inline_keyboard": [[
            {"text": "🔍 Nueva búsqueda", "callback_data": "nueva_busqueda"}
        ]]
    }

def botones_confirmacion():
    return {
        "inline_keyboard": [[
            {"text": "✅ Sí", "callback_data": "confirmar_si"},
            {"text": "❌ No, otro", "callback_data": "confirmar_no"}
        ], [
            {"text": "❌ Cancelar", "callback_data": "cancelar"}
        ]]
    }

def botones_lista(items, prefijo):
    botones = []
    for i, item in enumerate(items):
        label = item if isinstance(item, str) else item.get("label", str(i))
        botones.append([{"text": label, "callback_data": f"{prefijo}_{i}"}])
    botones.append([{"text": "❌ Cancelar", "callback_data": "cancelar"}])
    return {"inline_keyboard": botones}

def botones_analisis(resultados):
    botones = []
    for i, r in enumerate(resultados):
        botones.append([{
            "text": f"{i+1}️⃣ {r['tipo_analisis']}",
            "callback_data": f"analisis_{i}"
        }])
    botones.append([{"text": "📦 Todos", "callback_data": "analisis_todos"}])
    botones.append([{"text": "❌ Cancelar", "callback_data": "cancelar"}])
    return {"inline_keyboard": botones}

# ============================================
# MENSAJE DE BIENVENIDA
# ============================================

async def enviar_bienvenida(chat_id):
    texto = (
        "🌿 *Bienvenido al Bot de Análisis Fruglobe*\n\n"
        "Consulta los resultados de análisis de palta\n"
        "de forma rápida y sencilla.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "¿Cómo deseas buscar?\n"
    )
    await send_message(chat_id, texto, botones_inicio())

# ============================================
# FLUJO - MODO FECHA + NOMBRE
# ============================================

async def iniciar_modo_fecha(chat_id, user_id):
    user_state[user_id] = {
        "modo": "fecha_nombre",
        "estado": "esperando_busqueda",
        "ultima_actividad": datetime.now()
    }
    await send_message(
        chat_id,
        "📅 *Búsqueda por fecha y nombre*\n\n"
        "Escribe la fecha y el nombre del productor:\n\n"
        "Ej: `21/11/2025 MARIO PAMPAS`",
        botones_cancelar()
    )

# ============================================
# FLUJO - MODO SOLO NOMBRE
# ============================================

async def iniciar_modo_nombre(chat_id, user_id):
    user_state[user_id] = {
        "modo": "solo_nombre",
        "estado": "esperando_nombre",
        "ultima_actividad": datetime.now()
    }
    await send_message(
        chat_id,
        "👤 *Búsqueda por nombre*\n\n"
        "Escribe el nombre del productor:\n\n"
        "Ej: `MARIO PAMPAS`",
        botones_cancelar()
    )

# ============================================
# ENVIAR RESULTADO CON PDF
# ============================================

async def enviar_resultado(chat_id, r):
    link = get_hyperlink(r["fila"])
    texto = (
        f"🔬 *{r['tipo_analisis']}*\n"
        f"👤 {r['productor']}\n"
        f"📅 {r['fecha']}\n"
    )
    if link:
        texto += f"📄 [Ver PDF]({link})"
    else:
        texto += "📄 PDF no disponible"
    await send_message(chat_id, texto)

# ============================================
# MANEJO DE TEXTO
# ============================================

async def handle_text(chat_id, user_id, texto):

    # Verificar timeout
    if user_id in user_state and session_expirada(user_id):
        limpiar_sesion(user_id)
        await send_message(chat_id, "⏰ Tu sesión expiró. Iniciando nueva búsqueda...")
        await enviar_bienvenida(chat_id)
        return

    # Saludos → bienvenida
    if texto.lower().strip() in SALUDOS:
        limpiar_sesion(user_id)
        await enviar_bienvenida(chat_id)
        return

    # Sin sesión activa
    if user_id not in user_state:
        await enviar_bienvenida(chat_id)
        return

    estado = user_state[user_id]
    actualizar_sesion(user_id)
    modo = estado.get("modo")

    # ── MODO FECHA + NOMBRE ──
    if modo == "fecha_nombre" and estado["estado"] == "esperando_busqueda":
        partes = texto.split(maxsplit=1)

        if len(partes) < 2:
            await send_message(
                chat_id,
                "⚠️ Escribe fecha y nombre juntos:\nEj: `21/11/2025 MARIO PAMPAS`",
                botones_cancelar()
            )
            return

        fecha_str, nombre_str = partes[0], partes[1]

        if not parsear_fecha(fecha_str):
            await send_message(
                chat_id,
                "❌ Fecha inválida. Usa DD/MM/YYYY\nEj: `21/11/2025`",
                botones_cancelar()
            )
            return

        await send_message(chat_id, "⏳ Buscando en la base de datos...")
        resultados = buscar_por_fecha_y_nombre(fecha_str, nombre_str)

        if not resultados:
            # Sugerir por nombre solo
            sugerencias = buscar_por_nombre(nombre_str)
            if sugerencias:
                nombres = list(sugerencias.keys())[:5]
                texto_sug = f"❌ Sin resultados exactos para *{nombre_str}* en esa fecha.\n\n¿Quisiste decir alguno de estos?\n"
                estado["sugerencias"] = nombres
                estado["estado"] = "esperando_sugerencia"
                markup = botones_lista(nombres, "sugerencia")
                await send_message(chat_id, texto_sug, markup)
            else:
                await send_message(
                    chat_id,
                    f"❌ No encontré resultados para:\n📅 {fecha_str}\n👤 {nombre_str}",
                    botones_nueva_busqueda()
                )
            return

        estado["resultados"] = resultados
        estado["estado"] = "esperando_confirmacion"
        estado["index"] = 0

        r = resultados[0]
        await send_message(
            chat_id,
            f"¿Es *{r['productor']}* del *{r['fecha']}*?",
            botones_confirmacion()
        )

    # ── MODO SOLO NOMBRE ──
    elif modo == "solo_nombre" and estado["estado"] == "esperando_nombre":
        await send_message(chat_id, "⏳ Buscando en la base de datos...")
        nombres_encontrados = buscar_por_nombre(texto)

        if not nombres_encontrados:
            await send_message(
                chat_id,
                f"❌ No encontré ningún productor similar a *{texto}*.",
                botones_nueva_busqueda()
            )
            return

        nombres = list(nombres_encontrados.keys())[:8]
        estado["nombres_encontrados"] = nombres
        estado["estado"] = "esperando_nombre_seleccion"

        await send_message(
            chat_id,
            f"👤 Encontré *{len(nombres)}* productor(es) similar(es):\n\n¿Cuál buscas?",
            botones_lista(nombres, "nombre")
        )

# ============================================
# MANEJO DE CALLBACKS
# ============================================

async def handle_callback(chat_id, user_id, callback_data, callback_query_id):
    await answer_callback(callback_query_id)

    # Cancelar
    if callback_data == "cancelar":
        limpiar_sesion(user_id)
        await send_message(
            chat_id,
            "❌ Búsqueda cancelada.",
            botones_nueva_busqueda()
        )
        return

    # Nueva búsqueda
    if callback_data in ["nueva_busqueda", "modo_inicio"]:
        limpiar_sesion(user_id)
        await enviar_bienvenida(chat_id)
        return

    # Elegir modo
    if callback_data == "modo_fecha":
        await iniciar_modo_fecha(chat_id, user_id)
        return

    if callback_data == "modo_nombre":
        await iniciar_modo_nombre(chat_id, user_id)
        return

    # Verificar sesión activa
    if user_id not in user_state:
        await send_message(chat_id, "❌ Sesión expirada.")
        await enviar_bienvenida(chat_id)
        return

    estado = user_state[user_id]
    actualizar_sesion(user_id)

    # ── SUGERENCIA DE NOMBRE ──
    if callback_data.startswith("sugerencia_"):
        idx = int(callback_data.split("_")[1])
        nombres = estado.get("sugerencias", [])
        if idx >= len(nombres):
            return
        nombre_elegido = nombres[idx]
        await send_message(chat_id, "⏳ Buscando fechas disponibles...")

        fechas = buscar_fechas_por_productor(nombre_elegido)
        if not fechas:
            await send_message(
                chat_id,
                f"❌ No encontré análisis para *{nombre_elegido}*.",
                botones_nueva_busqueda()
            )
            return

        lista_fechas = list(fechas.keys())[:10]
        estado["productor_elegido"] = nombre_elegido
        estado["fechas_disponibles"] = lista_fechas
        estado["estado"] = "esperando_fecha_seleccion"

        await send_message(
            chat_id,
            f"👤 *{nombre_elegido}*\n\n📅 Fechas disponibles:",
            botones_lista(lista_fechas, "fecha")
        )

    # ── SELECCIÓN DE NOMBRE (modo solo nombre) ──
    elif callback_data.startswith("nombre_"):
        idx = int(callback_data.split("_")[1])
        nombres = estado.get("nombres_encontrados", [])
        if idx >= len(nombres):
            return
        nombre_elegido = nombres[idx]

        await send_message(chat_id, "⏳ Buscando fechas disponibles...")
        fechas = buscar_fechas_por_productor(nombre_elegido)

        if not fechas:
            await send_message(
                chat_id,
                f"❌ No encontré análisis para *{nombre_elegido}*.",
                botones_nueva_busqueda()
            )
            return

        lista_fechas = list(fechas.keys())[:10]
        estado["productor_elegido"] = nombre_elegido
        estado["fechas_disponibles"] = lista_fechas
        estado["estado"] = "esperando_fecha_seleccion"

        await send_message(
            chat_id,
            f"👤 *{nombre_elegido}*\n\n📅 Elige la fecha del análisis:",
            botones_lista(lista_fechas, "fecha")
        )

    # ── SELECCIÓN DE FECHA ──
    elif callback_data.startswith("fecha_"):
        idx = int(callback_data.split("_")[1])
        fechas = estado.get("fechas_disponibles", [])
        if idx >= len(fechas):
            return
        fecha_elegida = fechas[idx]
        productor = estado.get("productor_elegido", "")

        await send_message(chat_id, "⏳ Buscando análisis...")
        resultados = buscar_analisis_por_productor_fecha(productor, fecha_elegida)

        if not resultados:
            await send_message(
                chat_id,
                f"❌ No encontré análisis para *{productor}* en *{fecha_elegida}*.",
                botones_nueva_busqueda()
            )
            return

        estado["resultados"] = resultados
        estado["estado"] = "esperando_analisis"

        texto = f"👤 *{productor}*\n📅 {fecha_elegida}\n\n🔬 *Análisis disponibles:*\n\n"
        for i, r in enumerate(resultados):
            texto += f"{i+1}️⃣ {r['tipo_analisis']}\n"
        texto += "\n¿Cuál(es) quieres?"

        await send_message(chat_id, texto, botones_analisis(resultados))

    # ── CONFIRMACIÓN SÍ (modo fecha+nombre) ──
    elif callback_data == "confirmar_si":
        resultados = estado.get("resultados", [])
        texto = f"🔬 *Análisis disponibles:*\n\n"
        for i, r in enumerate(resultados):
            texto += f"{i+1}️⃣ {r['tipo_analisis']}\n"
        texto += "\n¿Cuál(es) quieres?"
        estado["estado"] = "esperando_analisis"
        await send_message(chat_id, texto, botones_analisis(resultados))

    # ── CONFIRMACIÓN NO (modo fecha+nombre) ──
    elif callback_data == "confirmar_no":
        estado["index"] = estado.get("index", 0) + 1
        resultados = estado.get("resultados", [])

        if estado["index"] >= len(resultados):
            await send_message(
                chat_id,
                "❌ No hay más resultados.",
                botones_nueva_busqueda()
            )
            limpiar_sesion(user_id)
            return

        r = resultados[estado["index"]]
        await send_message(
            chat_id,
            f"¿Es *{r['productor']}* del *{r['fecha']}*?",
            botones_confirmacion()
        )

    # ── SELECCIÓN ANÁLISIS ──
    elif callback_data == "analisis_todos":
        resultados = estado.get("resultados", [])
        await send_message(chat_id, f"📦 Enviando {len(resultados)} análisis...")
        for r in resultados:
            await enviar_resultado(chat_id, r)
        limpiar_sesion(user_id)
        await send_message(chat_id, "✅ Listo.", botones_nueva_busqueda())

    elif callback_data.startswith("analisis_"):
        idx = int(callback_data.split("_")[1])
        resultados = estado.get("resultados", [])
        if idx >= len(resultados):
            return
        await enviar_resultado(chat_id, resultados[idx])
        limpiar_sesion(user_id)
        await send_message(chat_id, "✅ Listo.", botones_nueva_busqueda())

# ============================================
# WEBHOOK
# ============================================

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.body()
        data = json.loads(body)
        print(f"📩 Webhook recibido")

        if "message" in data:
            message = data["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            if "text" in message:
                texto = message["text"].strip()
                print(f"Mensaje {user_id}: {texto}")
                await handle_text(chat_id, user_id, texto)

        elif "callback_query" in data:
            cb = data["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            user_id = cb["from"]["id"]
            callback_data = cb["data"]
            print(f"Callback {user_id}: {callback_data}")
            await handle_callback(chat_id, user_id, callback_data, cb["id"])

    except Exception as e:
        print(f"❌ Error webhook: {e}")
        import traceback
        traceback.print_exc()

    return JSONResponse({"ok": True})

@app.head("/webhook")
async def webhook_head():
    return JSONResponse({"ok": True})

@app.get("/")
async def health():
    return {"status": "ok", "bot": "fruglobe_analisis"}

@app.post("/")
async def root_post():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
