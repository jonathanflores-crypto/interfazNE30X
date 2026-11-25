"""Novus Monitor: ve lo que hacen las cámaras NE30x sin abrir su web.

Dos fuentes, guardadas en SQLite (data/monitor.db):
  - MQTT (novus.vision.event): face_recognized, face_unknown, device_status... La cámara empuja; no le cuesta nada.
  - Lectura ligera de GET /api/v1/faces/debug cada POLL_S segundos: cada intento de reconocimiento (puntaje, 2.º lugar,
    px entre ojos, plantilla). Sin video. Y cada 30 s GET /api/v1/faces para la salud (fps, ms, contadores).

Las fotos de los intentos se bajan de la cámara al momento y se guardan aquí (datos biométricos: se quedan en esta Mac).
"""
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time

import paho.mqtt.client as mqtt
import requests
from flask import Flask, Response, jsonify, render_template, request

DB = os.environ.get("DB_PATH", "monitor.db")
CAMARAS_JSON = os.path.join(os.path.dirname(DB) or ".", "camaras.json")


def cargar_camaras():
    """Lista de cámaras: la que se eligió en la página (data/camaras.json); la primera vez, la del .env."""
    try:
        with open(CAMARAS_JSON) as fh:
            return [c for c in json.load(fh) if isinstance(c, str)]
    except (OSError, ValueError):
        return [c.strip() for c in os.environ.get("CAMARAS", "").split(",") if c.strip()]


def guardar_camaras():
    with open(CAMARAS_JSON, "w") as fh:
        json.dump(CAMARAS, fh)


CAMARAS = cargar_camaras()
_hilos = {}   # ip -> turno del hilo que la lee; al quitarla (o volver a agregarla) el hilo viejo se sale solo
AUTH = (os.environ.get("CAM_USER", "admin"), os.environ.get("CAM_PASS", ""))
POLL_S = float(os.environ.get("POLL_S", "3"))
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "novus-monitor-ne301")   # distinto por contenedor: con el mismo id Mosquitto los tumba entre sí

app = Flask(__name__)
_lock = threading.Lock()
estado = {"mqtt": "sin conectar", "mqtt_msgs": 0, "prueba": "", "camaras": {}}
ciclo_testigos = crop_testigo = None   # firmware 964 (testigos.py), se instalan al arrancar
calidad_de = None                      # calidad.py: control de calidad del registro


def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(intentos)")]
        if cols and "arranque" not in cols:
            # La cámara vuelve a numerar desde 1 al reiniciar: la clave ahora incluye el arranque
            con.execute("ALTER TABLE intentos RENAME TO intentos_v1")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS intentos (
            id INTEGER PRIMARY KEY, ts REAL, camara TEXT, seq INTEGER, resultado TEXT, persona TEXT, score REAL,
            segundo TEXT, score2 REAL, plantilla TEXT, ojos REAL, conf REAL, umbral REAL, prueba TEXT,
            arranque INTEGER DEFAULT 0, UNIQUE(camara, arranque, seq));
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY, ts REAL, camara TEXT, tipo TEXT, persona TEXT, score REAL, tiempo_ms INTEGER,
            prueba TEXT, raw TEXT);
        CREATE TABLE IF NOT EXISTS salud (
            id INTEGER PRIMARY KEY, ts REAL, camara TEXT, fuente TEXT, ai_fps REAL, detect_ms REAL,
            embedding_ms REAL, norm_ms REAL, bateria REAL, raw TEXT);
        """)
        if con.execute("SELECT name FROM sqlite_master WHERE name='intentos_v1'").fetchone():
            con.execute("INSERT OR IGNORE INTO intentos (ts, camara, seq, resultado, persona, score, segundo, score2, "
                        "plantilla, ojos, conf, umbral, prueba, arranque) SELECT ts, camara, seq, resultado, persona, "
                        "score, segundo, score2, plantilla, ojos, conf, umbral, prueba, CASE WHEN seq > 3 THEN 0 ELSE 1 END "
                        "FROM intentos_v1 ORDER BY id")
            con.execute("DROP TABLE intentos_v1")
        if "foto" not in [r[1] for r in con.execute("PRAGMA table_info(intentos)")]:
            con.execute("ALTER TABLE intentos ADD COLUMN foto TEXT")   # ok / perdida (NULL: antes de bajarlas al momento)


# ---------------- MQTT ----------------
# App 967: guía del registro quieto por MQTT (enroll_guidance, ~3 por segundo) y su resultado (enroll_result).
# La guía no se guarda en la base: solo la última de cada cámara, en memoria.
guias = {}       # ip (o nombre de la cámara si aún no se sabe su ip) -> {"guia": data, "t": ts, "resultado": data, "tr": ts}
ip_de_camara = {}  # nombre/serie MQTT -> ip (de device_status)


def guardar_guia(cam, dev, ev):
    data = ev.get("data", {}) or {}
    clave = ip_de_camara.get(cam) or ip_de_camara.get(dev.get("serial")) or cam
    g = guias.setdefault(clave, {})
    if ev.get("type") == "enroll_guidance":
        g.update(guia=data, t=time.time())
    else:
        g.update(resultado=data, tr=time.time())


def on_connect(cli, _u, _f, rc, _p=None):
    estado["mqtt"] = "conectado" if rc == 0 else f"error {rc}"
    cli.subscribe("#")


def on_message(_cli, _u, msg):
    try:
        ev = json.loads(msg.payload.decode("utf-8", "replace"))
    except ValueError:
        return
    if not isinstance(ev, dict) or ev.get("schema") != "novus.vision.event":
        return
    estado["mqtt_msgs"] += 1
    dev = ev.get("device", {})
    cam = dev.get("name") or dev.get("serial") or msg.topic
    data = ev.get("data", {}) or {}
    perf = ev.get("performance", {}) or {}
    persona = (data.get("person") or {}).get("name")
    if ev.get("type") == "device_status" and data.get("ip"):
        ip_de_camara[cam] = data["ip"]
        if dev.get("serial"):
            ip_de_camara[dev["serial"]] = data["ip"]
    if ev.get("type") in ("enroll_guidance", "enroll_result"):
        guardar_guia(cam, dev, ev)
        if ev.get("type") == "enroll_guidance":
            return
        persona = data.get("name")
    with _lock, db() as con:
        con.execute("INSERT INTO eventos (ts, camara, tipo, persona, score, tiempo_ms, prueba, raw) VALUES (?,?,?,?,?,?,?,?)",
                    (time.time(), cam, ev.get("type"), persona, data.get("similarity"),
                     data.get("time_to_identify_ms"), estado["prueba"], json.dumps(ev)))
        if perf:
            con.execute("INSERT INTO salud (ts, camara, fuente, ai_fps, detect_ms, embedding_ms, norm_ms, bateria, raw) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (time.time(), cam, "mqtt", perf.get("ai_fps"), perf.get("detect_ms"), perf.get("embedding_ms"),
                         perf.get("norm_ms"), (data.get("battery") or {}).get("percent") if isinstance(data.get("battery"), dict) else None,
                         json.dumps(perf)))


def hilo_mqtt():
    while True:
        try:
            cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MQTT_CLIENT_ID)
            cli.on_connect = on_connect
            cli.on_message = on_message
            cli.connect(MQTT_HOST, MQTT_PORT, 60)
            cli.loop_forever()
        except Exception as e:  # noqa: BLE001
            estado["mqtt"] = f"sin broker ({e.__class__.__name__})"
            time.sleep(10)


# ---------------- Lectura ligera ----------------
def leer_versiones(ip, info):
    """Versiones de firmware, modelo cargado y hardware (cada 5 min: cambian solo al actualizar la cámara)."""
    v = requests.get(f"http://{ip}/api/v1/device/firmware-versions", auth=AUTH, timeout=8).json()["data"]
    d = requests.get(f"http://{ip}/api/v1/device/info", auth=AUTH, timeout=8).json()["data"]
    m = (requests.get(f"http://{ip}/api/v1/ai/status", auth=AUTH, timeout=8).json()["data"] or {}).get("model") or {}
    info["versiones"] = dict(
        app=v.get("app"), web=v.get("web"), modelo=v.get("model"), fsbl=v.get("fsbl"), wifi=v.get("wifi_running") or v.get("wifi"),
        wakecore=v.get("wakecore"), detector=m.get("name"), detector_version=m.get("version"),
        detector_kb=round(m["model_size"] / 1024) if m.get("model_size") else None, detector_estado=m.get("status"),
        stedgeai=m.get("stedgeai_version"), hardware=d.get("hardware_version"), sensor=d.get("camera_module"),
        energia=d.get("power_supply_type"), bateria=d.get("battery_percent"), red=d.get("communication_type"))
    info.update(nombre=d.get("device_name"), app=v.get("app"), web=v.get("web"), versiones_ts=time.time())


def archivo_foto(ip, arranque, seq):
    """Foto de un intento en el servidor; el nombre lleva el arranque porque la cámara vuelve a numerar al reiniciar."""
    return os.path.join(os.path.dirname(DB), "fotos", ip, f"{arranque}_{seq}.bmp")


def bajar_foto(ip, arranque, seq):
    """Baja la foto de un intento en cuanto se ve (la cámara solo guarda las últimas 8). Devuelve ok / perdida."""
    archivo = archivo_foto(ip, arranque, seq)
    if os.path.exists(archivo):
        return "ok"
    try:
        r = requests.get(f"http://{ip}/api/v1/faces/debug/picture", params={"seq": seq}, auth=AUTH, timeout=5)
    except requests.RequestException:
        return None           # la cámara no contestó: se reintenta en la siguiente vuelta
    if r.status_code != 200 or not r.content.startswith(b"BM"):
        return "perdida"
    os.makedirs(os.path.dirname(archivo), exist_ok=True)
    with open(archivo + ".tmp", "wb") as fh:
        fh.write(r.content)
    os.replace(archivo + ".tmp", archivo)
    return "ok"


def bajar_fotos_pendientes(ip, arranque, seqs_en_camara):
    """Todas las fotos de intentos del arranque actual que aún no están en el servidor, de la más vieja a la nueva."""
    with db() as con:
        pend = [r[0] for r in con.execute("SELECT seq FROM intentos WHERE camara=? AND arranque=? AND foto IS NULL "
                                          "ORDER BY seq", (ip, arranque))]
    for seq in pend:
        res = bajar_foto(ip, arranque, seq) if seq in seqs_en_camara else "perdida"
        if res is None:
            return
        with _lock, db() as con:
            con.execute("UPDATE intentos SET foto=? WHERE camara=? AND arranque=? AND seq=?", (res, ip, arranque, seq))


def hilo_camara(ip, turno):
    ultimo_seq = 0
    ultima_salud = 0.0
    with db() as con:   # arranque actual de la cámara (cambia cuando su numeración vuelve a empezar)
        arranque, seq_guardado = con.execute(
            "SELECT COALESCE(MAX(arranque), 0), COALESCE(MAX(seq), 0) FROM intentos WHERE camara=? "
            "AND arranque=(SELECT MAX(arranque) FROM intentos WHERE camara=?)", (ip, ip)).fetchone()
    revisar_arranque = seq_guardado > 0   # al arrancar el monitor: ¿la cámara se reinició mientras estaba apagado?
    while True:
        if _hilos.get(ip) != turno:
            return            # se quitó la cámara en la página
        info = estado["camaras"].setdefault(ip, {"ok": False, "error": "", "visto": 0})
        if time.time() < estado.get("pausa_hasta", 0):
            time.sleep(0.5)       # el banco está registrando: no se le carga la cámara
            continue
        try:
            r = requests.get(f"http://{ip}/api/v1/faces/debug", auth=AUTH, timeout=5)
            crops = r.json()["data"]["crops"]
            ahora = time.time()
            if crops and revisar_arranque:
                revisar_arranque = False
                if max(c["seq"] for c in crops) < seq_guardado:
                    arranque += 1      # numeración nueva: si no, los seq repetidos de la sesión anterior se ignoran
                else:
                    ultimo_seq = seq_guardado
            if crops and ultimo_seq and max(c["seq"] for c in crops) < ultimo_seq:
                arranque += 1          # la cámara reinició: su numeración empezó de nuevo
                ultimo_seq = 0
            nuevos = [c for c in crops if c["seq"] > ultimo_seq] if ultimo_seq else crops
            with _lock, db() as con:
                for c in sorted(nuevos, key=lambda c: c["seq"]):
                    con.execute(
                        "INSERT OR IGNORE INTO intentos (ts, camara, seq, resultado, persona, score, segundo, score2, "
                        "plantilla, ojos, conf, umbral, prueba, arranque, emb) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ahora - c.get("age_ms", 0) / 1000.0, ip, c["seq"], c.get("result"), c.get("best"),
                         c.get("score"), c.get("second"), c.get("score2"), c.get("template"), c.get("eye_px"),
                         c.get("conf"), c.get("threshold"), estado["prueba"], arranque,
                         json.dumps(c["embedding"]) if c.get("embedding") else None))
            if crops:
                ultimo_seq = max(ultimo_seq, max(c["seq"] for c in crops))
            bajar_fotos_pendientes(ip, arranque, {c["seq"] for c in crops})
            info.update(ok=True, error="", visto=ahora)
            if ahora - ultima_salud >= 30:
                ultima_salud = ahora
                s = requests.get(f"http://{ip}/api/v1/faces", auth=AUTH, timeout=8).json()["data"]
                pf = s.get("performance", {})
                info["faces"] = {k: s.get(k) for k in ("threshold", "margin", "confirm", "period_ms", "match", "norm",
                                                       "fotos", "active_count", "quality", "fotos_status", "testigos")}
                info["performance"] = pf
                info["people"] = s.get("people", [])
                sincronizar_galeria(ip, info["people"])
                if ciclo_testigos:
                    try:
                        ciclo_testigos(ip, s)
                    except Exception as e:  # noqa: BLE001  (se reintenta en 30 s)
                        info["testigos_error"] = e.__class__.__name__
                with _lock, db() as con:
                    con.execute("INSERT INTO salud (ts, camara, fuente, ai_fps, detect_ms, embedding_ms, norm_ms, raw) "
                                "VALUES (?,?,?,?,?,?,?,?)", (ahora, ip, "api", pf.get("ai_fps"), pf.get("detect_ms"),
                                                             pf.get("embedding_ms"), pf.get("norm_ms"), json.dumps(s.get("quality"))))
                if ahora - info.get("versiones_ts", 0) >= 300:
                    leer_versiones(ip, info)
        except Exception as e:  # noqa: BLE001
            info.update(ok=False, error=e.__class__.__name__)
        time.sleep(POLL_S)


# ---------------- Galería (fotos de registro, bajadas una vez por registro) ----------------
def bmp_calidad(b):
    """Brillo y contraste (media y desviación de los bytes) de un BMP de 24 bits"""
    off = int.from_bytes(b[10:14], "little")
    px = b[off:]
    n = len(px) or 1
    m = sum(px) / n
    return round(m), round((sum((x - m) ** 2 for x in px) / n) ** 0.5)


def galeria_dir(ip, pid):
    return os.path.join(os.path.dirname(DB), "galeria", ip, str(pid))


def sincronizar_galeria(ip, people):
    """Baja las fotos de registro de cada persona nueva o registrada de nuevo (cambia 'created')."""
    for p in people:
        if re.match(r"^L\d{3}_", p.get("name", "")):
            continue          # personas del banco sintético (LFW): sus fotos ya están en la Mac
        carpeta = galeria_dir(ip, p["id"])
        meta_f = os.path.join(carpeta, "meta.json")
        try:
            with open(meta_f) as fh:
                meta = json.load(fh)
            if meta.get("created") == p.get("created"):
                continue
        except (OSError, ValueError):
            pass
        try:
            sm = requests.get(f"http://{ip}/api/v1/faces/samples", params={"id": p["id"]}, auth=AUTH, timeout=8).json()
            fotos = []
            os.makedirs(carpeta, exist_ok=True)
            if any("testigo" in smp for smp in sm["data"]["samples"]):
                # Firmware 964: la cámara ya no guarda las fotos; cada una llega como testigo (testigos.py)
                fotos = [dict(k=smp["k"], score=smp.get("score"), plantilla=smp.get("template"),
                              testigo=smp["testigo"]) for smp in sm["data"]["samples"]]
                with open(meta_f, "w") as fh:
                    json.dump(dict(created=p.get("created"), fotos=fotos), fh)
                continue
            for f in os.listdir(carpeta):
                if f.endswith(".bmp"):
                    os.remove(os.path.join(carpeta, f))
            for smp in sm["data"]["samples"]:
                r = requests.get(f"http://{ip}/api/v1/faces/sample", params={"id": p["id"], "k": smp["k"]},
                                 auth=AUTH, timeout=8)
                if r.status_code == 200 and r.content.startswith(b"BM"):
                    with open(os.path.join(carpeta, f"{smp['k']}.bmp"), "wb") as fh:
                        fh.write(r.content)
                    br, ct = bmp_calidad(r.content)
                    fotos.append(dict(k=smp["k"], score=smp.get("score"), plantilla=smp.get("template"),
                                      brillo=br, contraste=ct))
            with open(meta_f, "w") as fh:
                json.dump(dict(created=p.get("created"), fotos=fotos), fh)
        except Exception:  # noqa: BLE001  (se reintenta en la siguiente lectura)
            continue


def camara_pedida():
    """?camara=<ip> para ver una sola cámara; vacío o desconocida = todas."""
    ip = request.args.get("camara", "")
    return ip if ip in CAMARAS else ""


@app.route("/api/galeria")
def api_galeria():
    out = []
    cam = camara_pedida()
    for ip in ([cam] if cam else CAMARAS):
        for p in estado["camaras"].get(ip, {}).get("people", []):
            try:
                with open(os.path.join(galeria_dir(ip, p["id"]), "meta.json")) as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {"fotos": []}
            for f in meta.get("fotos", []):   # listo: la foto ya está en esta Mac (si no, la página la vuelve a pedir)
                f["listo"] = os.path.exists(os.path.join(galeria_dir(ip, p["id"]), f"{f['k']}.bmp")) or bool(
                    f.get("testigo") and crop_testigo and crop_testigo(ip, f["testigo"]))
            out.append(dict(camara=ip, id=p["id"], nombre=p["name"], activa=p.get("active", True),
                            plantillas=p.get("templates", []), creado=p.get("created"), fotos=meta.get("fotos", []),
                            calidad=calidad_de(ip, p["id"]) if calidad_de else None))
    return jsonify(out)


@app.route("/galeria/<ip>/<int:pid>/<int:k>.bmp")
def galeria_foto(ip, pid, k):
    archivo = os.path.join(galeria_dir(ip, pid), f"{k}.bmp")
    if ip in CAMARAS and not os.path.exists(archivo) and crop_testigo:
        try:     # firmware 964: la foto es el testigo ya bajado
            with open(os.path.join(galeria_dir(ip, pid), "meta.json")) as fh:
                seq = next((f.get("testigo") for f in json.load(fh)["fotos"] if f["k"] == k), None)
            archivo = (crop_testigo(ip, seq) if seq else None) or archivo
        except (OSError, ValueError, KeyError):
            pass
    if ip not in CAMARAS or not os.path.exists(archivo):
        return "no está", 404
    with open(archivo, "rb") as fh:
        return Response(fh.read(), mimetype="image/bmp")


# ---------------- Cámaras (se agregan y quitan desde la página) ----------------
def iniciar_camara(ip):
    estado["camaras"].setdefault(ip, {"ok": False, "error": "", "visto": 0})
    if POLL_S > 0:
        _hilos[ip] = _hilos.get(ip, 0) + 1
        threading.Thread(target=hilo_camara, args=(ip, _hilos[ip]), daemon=True).start()


@app.route("/api/camaras", methods=["GET", "POST"])
def api_camaras():
    """GET: lista. POST {"ip", "accion": "agregar"|"quitar"}; al agregar se prueba primero que conteste."""
    if request.method == "POST":
        b = request.json or {}
        ip, accion = (b.get("ip") or "").strip(), b.get("accion")
        if accion == "quitar":
            if ip in CAMARAS:
                CAMARAS.remove(ip)
                _hilos.pop(ip, None)
                estado["camaras"].pop(ip, None)
                guardar_camaras()
            return jsonify(ok=True, camaras=CAMARAS)
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
            return jsonify(error="IP no válida"), 400
        try:
            v = requests.get(f"http://{ip}/api/v1/device/firmware-versions", auth=AUTH, timeout=6).json()["data"]
            d = requests.get(f"http://{ip}/api/v1/device/info", auth=AUTH, timeout=6).json()["data"]
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"la cámara {ip} no contesta ({e.__class__.__name__}); revisa la IP, la red o la contraseña"), 400
        if ip not in CAMARAS:
            CAMARAS.append(ip)
            guardar_camaras()
            iniciar_camara(ip)
        estado["camaras"][ip].update(nombre=d.get("device_name"), app=v.get("app"), web=v.get("web"))
        return jsonify(ok=True, camaras=CAMARAS, nombre=d.get("device_name"), app=v.get("app"), web=v.get("web"))
    return jsonify(camaras=[dict(ip=ip, **{k: estado["camaras"].get(ip, {}).get(k) for k in ("ok", "error", "nombre", "app", "web")})
                            for ip in CAMARAS])


# ---------------- Configuración de la cámara (thresholds sin abrir su web) ----------------
# campo del POST /faces/config -> cómo se lee en GET /faces
CONFIG_CAMPOS = {
    "threshold": ("threshold",), "margin": ("margin",), "confirm": ("confirm",), "period_ms": ("period_ms",),
    "match": ("match",), "norm": ("norm",), "fotos": ("fotos",), "show_pictures": ("show_pictures",),
    "quality_min_eye": ("quality", "min_eye_px"), "quality_min_conf": ("quality", "min_conf"),
    "quality_max_yaw": ("quality", "max_yaw"), "quality_min_luma": ("quality", "min_luma"),
    "strong_score": ("quality", "strong_score"), "strong_margin": ("quality", "strong_margin"),
    "enroll_min_eye": ("enroll_check", "min_eye_px"),
}
YUNET_MIN = 20   # en la NE302, bajar YuNet a 10 dejó de detectar del todo


def leer_config(ip):
    d = requests.get(f"http://{ip}/api/v1/faces", auth=AUTH, timeout=10).json()["data"]
    out = {}
    for campo, ruta in CONFIG_CAMPOS.items():
        v = d
        for k in ruta:
            v = v.get(k) if isinstance(v, dict) else None
        out[campo] = round(v, 3) if isinstance(v, float) else v
    ai = requests.get(f"http://{ip}/api/v1/ai/params", auth=AUTH, timeout=10).json()["data"]
    out["yunet_confidence"] = ai.get("confidence_threshold")
    return out


def corrida_en_curso():
    with db() as con:
        return con.execute("SELECT corrida FROM pasadas WHERE t_fin IS NULL AND t_ini > ? ORDER BY id DESC LIMIT 1",
                           (time.time() - 120,)).fetchone()


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        ip = request.args.get("camara", "")
        if ip not in CAMARAS:
            return jsonify(error="elige una cámara"), 400
        try:
            return jsonify(config=leer_config(ip))
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"la cámara no contesta ({e.__class__.__name__})"), 502
    b = request.json or {}
    ip, cambios = b.get("ip", ""), dict(b.get("cambios") or {})
    if ip not in CAMARAS:
        return jsonify(error="elige una cámara"), 400
    c = corrida_en_curso()
    if c:
        return jsonify(error=f"hay una corrida del banco en curso ({c[0]}): espera a que termine"), 409
    yunet = cambios.pop("yunet_confidence", None)
    cambios = {k: v for k, v in cambios.items() if k in CONFIG_CAMPOS}
    try:
        if yunet is not None:
            if int(yunet) < YUNET_MIN:
                return jsonify(error=f"YuNet no se baja de {YUNET_MIN} (en la NE302 dejó de detectar)"), 400
            r = requests.post(f"http://{ip}/api/v1/ai/params", json={"confidence_threshold": int(yunet)},
                              auth=AUTH, timeout=10).json()
            if not r.get("success"):
                return jsonify(error="YuNet: " + str(r.get("message"))), 400
        if cambios:
            r = requests.post(f"http://{ip}/api/v1/faces/config", json=cambios, auth=AUTH, timeout=10).json()
            if not r.get("success"):
                return jsonify(error=str(r.get("message"))), 400
        return jsonify(ok=True, config=leer_config(ip))   # lo que quedó guardado en la cámara
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"la cámara no contesta ({e.__class__.__name__})"), 502


# ---------------- Registrar y borrar personas sin abrir la web de la cámara ----------------
def refrescar_personas(ip, d=None):
    """Vuelve a leer la lista de personas al momento (si no, la galería tarda hasta 30 s en enterarse)."""
    d = d or requests.get(f"http://{ip}/api/v1/faces", auth=AUTH, timeout=10).json()["data"]
    info = estado["camaras"].setdefault(ip, {"ok": False, "error": "", "visto": 0})
    info["people"] = d.get("people", [])
    threading.Thread(target=sincronizar_galeria, args=(ip, info["people"]), daemon=True).start()
    return d


@app.route("/api/registro", methods=["GET", "POST"])
def api_registro():
    """Registro quieto (estático, la persona parada a ~2 m). GET ?camara=ip: cómo va. POST {ip, nombre} o {ip, accion: cancelar}."""
    if request.method == "GET":
        ip = request.args.get("camara", "")
        if ip not in CAMARAS:
            return jsonify(error="elige una cámara"), 400
        try:
            d = requests.get(f"http://{ip}/api/v1/faces", auth=AUTH, timeout=10).json()["data"]
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"la cámara no contesta ({e.__class__.__name__})"), 502
        enr = d.get("enrollment", {})
        if enr.get("active"):
            estado["pausa_hasta"] = time.time() + 5     # mientras registra, el monitor no le carga la cámara
        else:
            refrescar_personas(ip, d)
        return jsonify(enrollment=enr)
    b = request.json or {}
    ip = b.get("ip", "")
    if ip not in CAMARAS:
        return jsonify(error="elige una cámara"), 400
    if corrida_en_curso():
        return jsonify(error="hay una corrida del banco en curso: espera a que termine"), 409
    try:
        if b.get("accion") == "cancelar":
            r = requests.post(f"http://{ip}/api/v1/faces/enroll/cancel", json={}, auth=AUTH, timeout=10).json()
        else:
            nombre = (b.get("nombre") or "").strip()
            if not nombre or len(nombre) > 31:
                return jsonify(error="nombre de 1 a 31 caracteres"), 400
            estado["pausa_hasta"] = time.time() + 5
            r = requests.post(f"http://{ip}/api/v1/faces/enroll", json={"name": nombre, "still": True},
                              auth=AUTH, timeout=10).json()
        if not r.get("success"):
            return jsonify(error=str(r.get("message"))), 400
        return jsonify(ok=True)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"la cámara no contesta ({e.__class__.__name__})"), 502


@app.route("/api/registro/guia")
def api_registro_guia():
    """Última guía del registro quieto que mandó la cámara por MQTT (no consulta la cámara)."""
    ip = request.args.get("camara", "")
    g = guias.get(ip)
    if g is None:
        # sin device_status todavía: la única cámara que mandó guía hace poco
        recientes = [v for v in guias.values() if time.time() - v.get("t", 0) < 5]
        g = recientes[0] if len(recientes) == 1 else {}
    ahora = time.time()
    return jsonify(guia=g.get("guia"), hace_s=round(ahora - g["t"], 1) if "t" in g else None,
                   resultado=g.get("resultado"), resultado_hace_s=round(ahora - g["tr"], 1) if "tr" in g else None)


@app.route("/api/borrar", methods=["POST"])
def api_borrar():
    b = request.json or {}
    ip, pid = b.get("ip", ""), b.get("id")
    if ip not in CAMARAS or not isinstance(pid, int):
        return jsonify(error="falta cámara o id"), 400
    if corrida_en_curso():
        return jsonify(error="hay una corrida del banco en curso: espera a que termine"), 409
    try:
        r = requests.post(f"http://{ip}/api/v1/faces/delete", json={"id": pid}, auth=AUTH, timeout=60).json()
        if not r.get("success"):
            return jsonify(error=str(r.get("message"))), 400
        refrescar_personas(ip)
        return jsonify(ok=True)
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"la cámara no contesta ({e.__class__.__name__})"), 502


# ---------------- Web ----------------
@app.route("/")
def inicio():
    return render_template("index.html", camaras=CAMARAS)


def nombres_mqtt(cam):
    """Los eventos MQTT llegan con el nombre de la cámara (NE301-2A1959), no con su IP. Todas las cámaras publican
    en el mismo broker: se filtran por los nombres de las cámaras de este monitor ([] = aún no se conocen)."""
    return [n for n in (estado["camaras"].get(ip, {}).get("nombre") for ip in ([cam] if cam else CAMARAS)) if n]


def filtro_eventos(cam):
    nombres = nombres_mqtt(cam)
    return (f" AND camara IN ({','.join('?' * len(nombres))})", nombres) if nombres else ("", [])


@app.route("/api/estado")
def api_estado():
    desde = int(request.args.get("desde", 0))
    cam = camara_pedida()
    sql, args = filtro_eventos(cam)
    with db() as con:
        intentos = [dict(r) for r in con.execute(
            "SELECT * FROM intentos WHERE id > ? AND (?='' OR camara=?) ORDER BY id DESC LIMIT 200", (desde, cam, cam))]
        eventos = [dict(r) for r in con.execute(
            "SELECT id, ts, camara, tipo, persona, score, tiempo_ms, prueba FROM eventos WHERE 1" + sql +
            " ORDER BY id DESC LIMIT 50", args)]
    return jsonify(estado=estado, intentos=intentos, eventos=eventos)


@app.route("/api/rendimiento")
def api_rendimiento():
    """Contadores de hoy y tiempo al reconocimiento (time_to_identify_ms de los eventos face_recognized), más el
    último evento MQTT completo (metadata)."""
    sql, args = filtro_eventos(camara_pedida())
    with db() as con:
        ult = con.execute("SELECT raw FROM eventos WHERE 1" + sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        ultimo = json.loads(ult["raw"]) if ult else None
        off = (ultimo or {}).get("tz_offset_h", -6) * 3600          # hora local de la cámara
        hoy = (time.time() + off) // 86400 * 86400 - off
        conteo = dict(con.execute("SELECT tipo, COUNT(*) FROM eventos WHERE ts >= ?" + sql + " GROUP BY tipo",
                                  [hoy] + args).fetchall())
        tiempos = [r[0] for r in con.execute(
            "SELECT tiempo_ms FROM eventos WHERE tipo='face_recognized' AND tiempo_ms IS NOT NULL AND ts >= ?" + sql +
            " ORDER BY id DESC", [hoy] + args)]
        recientes = [dict(r) for r in con.execute(
            "SELECT ts, persona, score, tiempo_ms FROM eventos WHERE tipo='face_recognized' AND tiempo_ms IS NOT NULL" + sql +
            " ORDER BY id DESC LIMIT 10", args)]
    t = sorted(tiempos)
    pct = lambda q: t[min(len(t) - 1, int(q * len(t)))] if t else None   # noqa: E731
    return jsonify(hoy=dict(reconocidos=conteo.get("face_recognized", 0), desconocidos=conteo.get("face_unknown", 0),
                            tiempo=dict(n=len(t), ultimo=tiempos[0] if tiempos else None, mediana=pct(0.5), p90=pct(0.9),
                                        minimo=t[0] if t else None, maximo=t[-1] if t else None,
                                        promedio=round(sum(t) / len(t)) if t else None)),
                   recientes=recientes, ultimo=ultimo)


@app.route("/api/prueba", methods=["POST"])
def api_prueba():
    estado["prueba"] = (request.json or {}).get("nombre", "")[:40]
    return jsonify(ok=True, prueba=estado["prueba"])


@app.route("/api/resumen")
def api_resumen():
    """Por prueba y persona: intentos, mejor/mediana de puntaje y cuántos con nombre.
    ?limite=N: solo las N pruebas más recientes (la más nueva primero); sin limite, todas."""
    with db() as con:
        filas = [dict(r) for r in con.execute(
            "SELECT id, prueba, persona, resultado, score, score2, ojos FROM intentos WHERE prueba != '' "
            "AND (?='' OR camara=?)", (camara_pedida(),) * 2)]
    grupos, ultima = {}, {}
    for f in filas:
        grupos.setdefault((f["prueba"], f["persona"]), []).append(f)
        ultima[f["prueba"]] = max(ultima.get(f["prueba"], 0), f["id"])
    orden = sorted(ultima, key=ultima.get, reverse=True)
    limite = request.args.get("limite", type=int)
    pos = {p: i for i, p in enumerate(orden[:limite] if limite else orden)}
    out = []
    for (prueba, persona), g in sorted(((k, g) for k, g in grupos.items() if k[0] in pos),
                                       key=lambda kg: (pos[kg[0][0]], kg[0][1] or "")):
        sc = sorted(x["score"] or 0 for x in g)
        out.append(dict(prueba=prueba, persona=persona, intentos=len(g),
                        reconocido=sum(1 for x in g if x["resultado"] == "reconocido"),
                        score_max=round(sc[-1], 3), score_mediana=round(sc[len(sc) // 2], 3),
                        ventaja_mediana=round(sorted((x["score"] or 0) - (x["score2"] or 0) for x in g)[len(g) // 2], 3)))
    return jsonify(filas=out, pruebas=len(orden))


@app.route("/export.csv")
def export_csv():
    with db() as con:
        cam = camara_pedida()
        filas = [dict(r) for r in con.execute("SELECT * FROM intentos WHERE (?='' OR camara=?) ORDER BY id", (cam, cam))]
    buf = io.StringIO()
    if filas:
        w = csv.DictWriter(buf, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=intentos.csv"})


@app.route("/foto/<ip>/<int:arranque>/<int:seq>")
def foto_intento(ip, arranque, seq):
    """Foto de un intento guardada en el servidor (se baja de la cámara en cuanto aparece el intento)."""
    if ip not in CAMARAS:
        return "cámara desconocida", 404
    archivo = archivo_foto(ip, arranque, seq)
    if not os.path.exists(archivo):
        with db() as con:
            ultimo = con.execute("SELECT MAX(arranque) FROM intentos WHERE camara=?", (ip,)).fetchone()[0]
        if arranque == ultimo and bajar_foto(ip, arranque, seq) == "ok":
            pass
        else:
            return "foto perdida: la cámara la borró antes de que el servidor la bajara", 404
    with open(archivo, "rb") as fh:
        return Response(fh.read(), mimetype="image/bmp")


@app.route("/foto/<ip>/<int:seq>")
def foto(ip, seq):
    """Ruta vieja (antes de guardar por arranque): fotos que se abrieron a mano."""
    if ip not in CAMARAS:
        return "cámara desconocida", 404
    carpeta = os.path.join(os.path.dirname(DB), "fotos", ip)
    archivo = os.path.join(carpeta, f"{seq}.bmp")
    if os.path.exists(archivo):   # ya se pidió una vez: no se vuelve a molestar a la cámara
        with open(archivo, "rb") as fh:
            return Response(fh.read(), mimetype="image/bmp")
    r = requests.get(f"http://{ip}/api/v1/faces/debug/picture", params={"seq": seq}, auth=AUTH, timeout=8)
    if r.status_code != 200 or not r.content.startswith(b"BM"):
        return "ya no está en la cámara (guarda solo las últimas 8)", 404
    os.makedirs(carpeta, exist_ok=True)
    with open(archivo, "wb") as fh:
        fh.write(r.content)
    return Response(r.content, mimetype="image/bmp")


if __name__ == "__main__":
    init_db()
    import banco
    import testigos
    ciclo_testigos, crop_testigo = testigos.instalar(app, db, estado, AUTH, lambda: CAMARAS, os.path.dirname(DB) or ".")
    try:
        import calidad     # control de calidad del registro (necesita numpy/onnxruntime en la imagen)
        calidad_de = calidad.instalar(app, db, estado, AUTH, lambda: CAMARAS, os.path.dirname(DB) or ".")
    except ImportError as e:
        print("calidad del registro desactivada:", e)
    for ip in CAMARAS:
        iniciar_camara(ip)
    banco.instalar(app, db, estado, AUTH)
    threading.Thread(target=hilo_mqtt, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
