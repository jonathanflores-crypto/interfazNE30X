"""Firmware 964: testigos, respaldo/restauración de la galería y huellas (PCA).

- Testigos: cada foto de registro sale de la cámara como crop + embedding con un ID único
  (<serie>-<persona>-<n.º>-<hora unix>). El monitor los baja a data/testigos/<ID>/ (crop.bmp + testigo.json)
  y le confirma a la cámara (ack) para que los borre. Datos biométricos: se quedan en esta máquina.
- Respaldo: GET /faces/backup de cada persona → data/respaldos/<ip>/<fecha>/<id>.json (plantillas + embedding
  de cada foto). Restaurar los sube con POST /faces/restore, sin volver a calcular nada.
- Huellas: PCA 2D (en el navegador) de las fotos de registro y de los intentos, con la trayectoria de cada pasada.
"""
import datetime
import json
import os
import re
import time

import requests
from flask import Response, jsonify, render_template, request

LOTE = 50          # testigos por consulta
PASADA_S = 3.0     # intentos separados por menos de esto son la misma pasada


def instalar(app, db, estado, auth, camaras, datos_dir):
    testigos_dir = os.path.join(datos_dir, "testigos")
    respaldos_dir = os.path.join(datos_dir, "respaldos")

    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS testigos (
            tid TEXT PRIMARY KEY, camara TEXT, seq INTEGER, persona_id INTEGER, nombre TEXT, k INTEGER,
            plantilla TEXT, creado INTEGER, score REAL, emb TEXT, bajado REAL)""")
        cols = [r[1] for r in con.execute("PRAGMA table_info(intentos)")]
        if "emb" not in cols:
            con.execute("ALTER TABLE intentos ADD COLUMN emb TEXT")

    def cam(ip, metodo, ruta, timeout=15, **kw):
        return requests.request(metodo, f"http://{ip}/api/v1/{ruta}", auth=auth, timeout=timeout, **kw)

    # ---------------- Testigos ----------------
    def bajar_testigos(ip):
        """Baja hasta LOTE testigos pendientes y confirma los guardados. Devuelve (bajados, pendientes en la cámara)."""
        d = cam(ip, "GET", "faces/testigos", params={"limit": LOTE, "emb": 1}).json()["data"]
        hechos = []
        for t in d.get("testigos", []):
            if t.get("unreadable") or "id" not in t:
                hechos.append(t["seq"])     # archivo dañado en la cámara: se anota y se suelta
                with open(os.path.join(datos_dir, "testigos_ilegibles.log"), "a") as fh:
                    fh.write(f"{time.strftime('%F %T')} {ip} seq {t['seq']}\n")
                continue
            tid = re.sub(r"[^A-Za-z0-9_.-]", "_", t["id"])
            r = cam(ip, "GET", "faces/testigo/picture", params={"seq": t["seq"]})
            if r.status_code != 200 or not r.content.startswith(b"BM"):
                continue
            carpeta = os.path.join(testigos_dir, tid)
            os.makedirs(carpeta, exist_ok=True)
            with open(os.path.join(carpeta, "crop.bmp"), "wb") as fh:
                fh.write(r.content)
            meta = dict(t, camara=ip, bajado=time.time())
            with open(os.path.join(carpeta, "testigo.json"), "w") as fh:
                json.dump(meta, fh)
            with db() as con:
                con.execute("INSERT OR REPLACE INTO testigos VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (tid, ip, t["seq"], t.get("person_id"), t.get("name"), t.get("k"), t.get("template"),
                             t.get("created"), t.get("score"), json.dumps(t.get("embedding")), meta["bajado"]))
            hechos.append(t["seq"])
        if hechos:
            cam(ip, "POST", "faces/testigos/ack", json={"seqs": hechos})
        return len(hechos), d.get("pending", 0)

    def ciclo_testigos(ip, status):
        """Lo llama el hilo de cada cámara junto con la lectura de salud (cada 30 s)."""
        info = estado["camaras"].setdefault(ip, {})
        tg = status.get("testigos")
        if tg is None:
            return                       # firmware anterior a 964
        info["testigos"] = tg
        if estado.get("huellas_on") and not status.get("debug_embedding"):
            cam(ip, "POST", "faces/config", json={"debug_embedding": True})   # la cámara no lo guarda al reiniciar
        pendientes = tg.get("pending", 0)
        vueltas = 0
        while pendientes and vueltas < 4:     # hasta 200 por ciclo, para no acaparar la cámara
            n, pendientes = bajar_testigos(ip)
            if n == 0:
                break
            vueltas += 1
            info["testigos_bajados"] = info.get("testigos_bajados", 0) + n

    def crop_testigo(ip, seq):
        with db() as con:
            r = con.execute("SELECT tid FROM testigos WHERE camara=? AND seq=? ORDER BY bajado DESC LIMIT 1",
                            (ip, seq)).fetchone()
        return os.path.join(testigos_dir, r["tid"], "crop.bmp") if r else None

    @app.route("/api/testigos")
    def api_testigos():
        ip = request.args.get("camara", "")
        q = "SELECT tid, camara, seq, persona_id, nombre, k, plantilla, creado, score, bajado FROM testigos"
        args = ()
        if ip:
            q += " WHERE camara=?"
            args = (ip,)
        with db() as con:
            filas = [dict(r) for r in con.execute(q + " ORDER BY bajado DESC LIMIT 500", args)]
        return jsonify(testigos=filas, camaras={c: estado["camaras"].get(c, {}).get("testigos") for c in camaras()})

    @app.route("/testigo/<path:tid>/crop.bmp")
    def testigo_crop(tid):
        tid = re.sub(r"[^A-Za-z0-9_.-]", "_", tid)
        archivo = os.path.join(testigos_dir, tid, "crop.bmp")
        if not os.path.exists(archivo):
            return "no está", 404
        with open(archivo, "rb") as fh:
            return Response(fh.read(), mimetype="image/bmp")

    # ---------------- Respaldo y restauración ----------------
    def respaldar(ip):
        d = cam(ip, "GET", "faces").json()["data"]
        carpeta = os.path.join(respaldos_dir, ip, datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        os.makedirs(carpeta, exist_ok=True)
        ok, errores = 0, []
        for p in d.get("people", []):
            estado["pausa_hasta"] = time.time() + 5
            r = cam(ip, "GET", "faces/backup", params={"id": p["id"]})
            if r.status_code != 200:
                errores.append(f"{p['name']}: HTTP {r.status_code}")
                if r.status_code == 404 and ok == 0:
                    break             # firmware sin /faces/backup (antes de 964)
                continue
            with open(os.path.join(carpeta, f"{p['id']}.json"), "w") as fh:
                json.dump(r.json()["data"], fh)
            ok += 1
        cfg = {k: v for k, v in d.items() if k not in ("people", "enrollment", "recording", "last", "performance")}
        with open(os.path.join(carpeta, "indice.json"), "w") as fh:
            json.dump(dict(camara=ip, fecha=time.time(), personas=ok, errores=errores, config=cfg), fh)
        return dict(carpeta=os.path.relpath(carpeta, datos_dir), personas=ok, errores=errores)

    def lista_respaldos(ip):
        base = os.path.join(respaldos_dir, ip)
        out = []
        for f in sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []:
            try:
                with open(os.path.join(base, f, "indice.json")) as fh:
                    ind = json.load(fh)
            except (OSError, ValueError):
                continue
            out.append(dict(carpeta=f, personas=ind.get("personas"), errores=len(ind.get("errores", []))))
        return out

    @app.route("/api/respaldo", methods=["GET", "POST"])
    def api_respaldo():
        if request.method == "GET":
            ip = request.args.get("camara", "")
            return jsonify(respaldos=lista_respaldos(ip) if ip else [])
        b = request.get_json(force=True)
        ip = b.get("ip", "")
        if ip not in camaras():
            return jsonify(ok=False, error="cámara desconocida"), 400
        try:
            res = respaldar(ip)
        except Exception as e:  # noqa: BLE001
            return jsonify(ok=False, error=f"{e.__class__.__name__}: {e}"), 502
        return jsonify(ok=res["personas"] > 0, **res)

    @app.route("/api/restaurar", methods=["POST"])
    def api_restaurar():
        b = request.get_json(force=True)
        ip, nombre = b.get("ip", ""), os.path.basename(b.get("carpeta", ""))
        carpeta = os.path.join(respaldos_dir, ip, nombre)
        if ip not in camaras() or not nombre or not os.path.isdir(carpeta):
            return jsonify(ok=False, error="cámara o respaldo desconocido"), 400
        archivos = sorted(f for f in os.listdir(carpeta) if re.match(r"^\d+\.json$", f))
        ok, errores = 0, []
        for f in archivos:
            with open(os.path.join(carpeta, f)) as fh:
                persona = json.load(fh)
            persona["save"] = False          # se escribe una vez al final (commit)
            estado["pausa_hasta"] = time.time() + 5
            try:
                r = cam(ip, "POST", "faces/restore", json=persona, timeout=20).json()
                if r.get("success") is False:
                    errores.append(f"{persona.get('name')}: {r.get('message')}")
                else:
                    ok += 1
            except Exception as e:  # noqa: BLE001
                errores.append(f"{persona.get('name')}: {e.__class__.__name__}")
        try:
            cam(ip, "POST", "faces/restore", json={"commit": True}, timeout=60)
        except Exception as e:  # noqa: BLE001
            errores.append(f"commit: {e.__class__.__name__}")
        return jsonify(ok=not errores, personas=ok, errores=errores)

    # ---------------- Huellas (PCA) ----------------
    @app.route("/huellas")
    def huellas():
        return render_template("huellas.html", camaras=camaras())

    @app.route("/api/huellas", methods=["GET", "POST"])
    def api_huellas():
        if request.method == "POST":     # grabar (o no) el embedding de cada intento
            on = bool(request.get_json(force=True).get("on"))
            estado["huellas_on"] = on
            errores = []
            for ip in camaras():
                try:
                    cam(ip, "POST", "faces/config", json={"debug_embedding": on})
                except Exception as e:  # noqa: BLE001
                    errores.append(f"{ip}: {e.__class__.__name__}")
            return jsonify(ok=not errores, on=on, errores=errores)
        ip = request.args.get("camara", "")
        horas = float(request.args.get("horas", 2))
        galeria, fuente = [], ""
        resp = lista_respaldos(ip)
        if resp:                           # el último respaldo es la galería actual
            fuente = f"respaldo {resp[0]['carpeta']}"
            carpeta = os.path.join(respaldos_dir, ip, resp[0]["carpeta"])
            for f in os.listdir(carpeta):
                if not re.match(r"^\d+\.json$", f):
                    continue
                with open(os.path.join(carpeta, f)) as fh:
                    p = json.load(fh)
                for j, e in enumerate(p.get("fotos", [])):
                    galeria.append(dict(nombre=p["name"], pid=p["id"], tipo="foto", k=j + 1, emb=e["embedding"]))
                for t, e in enumerate(p.get("templates", [])):
                    if e:
                        galeria.append(dict(nombre=p["name"], pid=p["id"], tipo="plantilla", k=t, emb=e))
        else:                              # sin respaldo: los testigos bajados
            fuente = "testigos"
            with db() as con:
                for r in con.execute("SELECT nombre, persona_id, k, emb FROM testigos WHERE camara=?", (ip,)):
                    if r["emb"] and r["emb"] != "null":
                        galeria.append(dict(nombre=r["nombre"], pid=r["persona_id"], tipo="foto", k=r["k"],
                                            emb=json.loads(r["emb"])))
        with db() as con:
            filas = con.execute(
                "SELECT ts, seq, resultado, persona, score, segundo, score2, ojos, emb FROM intentos "
                "WHERE camara=? AND ts>=? AND emb IS NOT NULL ORDER BY ts", (ip, time.time() - horas * 3600)).fetchall()
        intentos, pasada, ultimo = [], 0, None
        for r in filas:
            if ultimo is None or r["ts"] - ultimo > PASADA_S:
                pasada += 1
            ultimo = r["ts"]
            intentos.append(dict(ts=r["ts"], seq=r["seq"], resultado=r["resultado"], persona=r["persona"],
                                 score=r["score"], segundo=r["segundo"], score2=r["score2"], ojos=r["ojos"],
                                 pasada=pasada, emb=json.loads(r["emb"])))
        return jsonify(fuente=fuente, galeria=galeria, intentos=intentos, grabando=bool(estado.get("huellas_on")))

    return ciclo_testigos, crop_testigo
