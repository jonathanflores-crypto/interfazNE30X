"""Banco sintético: caras de LFW en la pantalla de la Mac, frente a la cámara, que "caminan" hacia ella.

- Plan fijo (semilla): N personas se registran con su foto 1 (registro caminando) y se prueban con su foto 2;
  M personas nunca se registran (impostores). Nombres en la cámara: L001_Nombre...
- Cada pasada se guarda con su verdad (quién es, o "nadie"); los intentos que el monitor lee de la cámara en esa
  ventana de tiempo dan el resultado: nombre correcto, nombre equivocado, desconocido, falso positivo.
"""
import os
import random
import re
import statistics
import time

import requests
from flask import jsonify, render_template, request, send_from_directory

LFW = os.environ.get("LFW_DIR", "/lfw")
PREFIJO = re.compile(r"^L\d{3}_")


def _personas():
    out = []
    if not os.path.isdir(LFW):
        return out
    for n in sorted(os.listdir(LFW)):
        d = os.path.join(LFW, n)
        if os.path.isdir(d):
            fotos = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
            if fotos:
                out.append((n, fotos))
    return out


# Foto de registro distinta de la 1 cuando esa no sirve (otra cara en cuadro); la de prueba sigue siendo la 2
FOTO_REGISTRO = {"Roseanne_Barr": 2}   # Roseanne_Barr_0001 trae otra persona a la derecha y la cámara la rechaza


def plan(n_reg, n_imp, semilla=2026, k=1):
    """k = fotos distintas de registro por persona; se prueba con la foto k+1, que nunca se registró"""
    gente = _personas()
    rnd = random.Random(semilla)
    con2 = [p for p in gente if len(p[1]) >= k + 1]
    rnd.shuffle(con2)
    reg = con2[:n_reg]
    usados = {p[0] for p in reg}
    resto = [p for p in gente if p[0] not in usados]
    rnd.shuffle(resto)
    imp = resto[:n_imp]
    def de_registro(n, f):
        return [f"{n}/{f[FOTO_REGISTRO.get(n, 0)]}"] if k == 1 else [f"{n}/{f[j]}" for j in range(k)]
    registrados = [dict(nombre=f"L{i + 1:03d}_{n}"[:31], lfw=n, fotos_registro=de_registro(n, f),
                        foto_registro=de_registro(n, f)[0], foto_prueba=f"{n}/{f[k]}")
                   for i, (n, f) in enumerate(reg)]
    impostores = [dict(nombre="nadie", lfw=n, foto_prueba=f"{n}/{f[0]}") for n, f in imp]
    return registrados, impostores


def instalar(app, db, estado, auth):
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS pasadas (
            id INTEGER PRIMARY KEY, corrida TEXT, camara TEXT, verdad TEXT, lfw TEXT, t_ini REAL, t_fin REAL)""")
        # Tiempo de cada registro: del POST faces/enroll a que la cámara termina (se ve en el sondeo "estado")
        con.execute("""CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY, camara TEXT, nombre TEXT, modo TEXT, t_ini REAL, t_fin REAL, resultado TEXT,
            frente INTEGER, lado_a INTEGER, lado_b INTEGER, rechazadas INTEGER, ultimo_rechazo TEXT)""")
    en_curso = {}   # ip -> id de la fila de registros sin terminar

    def registro_fin(ip, e):
        rid = en_curso.get(ip)
        if not rid or e.get("active"):
            return
        en_curso.pop(ip, None)
        with db() as con:
            con.execute("UPDATE registros SET t_fin=?, resultado=?, frente=?, lado_a=?, lado_b=?, rechazadas=?, "
                        "ultimo_rechazo=? WHERE id=? AND t_fin IS NULL",
                        (time.time(), e.get("result"), int(bool(e.get("pose_front"))), int(bool(e.get("pose_side_a"))),
                         int(bool(e.get("pose_side_b"))), e.get("rejected"), e.get("last_rejected"), rid))

    def cam(ip, metodo, ruta, **kw):
        ultimo = None
        for _ in range(3):        # la cámara atiende una petición a la vez: se reintenta
            try:
                r = requests.request(metodo, f"http://{ip}/api/v1/{ruta}", auth=auth, timeout=12, **kw)
                return r.json()
            except Exception as e:  # noqa: BLE001
                ultimo = e
                time.sleep(0.7)
        raise ultimo

    @app.route("/banco")
    def banco():
        return render_template("banco.html", camaras=list(estado["camaras"].keys()) or [""])

    @app.route("/lfw/<path:ruta>")
    def lfw(ruta):
        return send_from_directory(LFW, ruta)

    @app.route("/api/banco/plan")
    def api_plan():
        reg, imp = plan(int(request.args.get("n", 100)), int(request.args.get("m", 100)),
                        int(request.args.get("semilla", 2026)), int(request.args.get("k", 1)))
        return jsonify(registrados=reg, impostores=imp, lfw_personas=len(_personas()))

    @app.route("/api/banco/camara", methods=["POST"])
    def api_camara():
        b = request.json or {}
        ip, acc = b.get("ip"), b.get("accion")
        if ip not in estado["camaras"]:
            return jsonify(error="cámara desconocida"), 400
        try:
            if acc == "estado":
                d = cam(ip, "GET", "faces")["data"]
                registro_fin(ip, d["enrollment"])
                return jsonify(enrollment=d["enrollment"], personas=len(d["people"]), max=d.get("max_people"),
                               activos=[p["id"] for p in d["people"] if p.get("active", True)],
                               banco=[p["id"] for p in d["people"] if PREFIJO.match(p["name"])],
                               nombres={p["name"]: p["id"] for p in d["people"]},
                               plantillas={p["name"]: p.get("templates", []) for p in d["people"]})
            if acc == "pausa":         # el monitor deja de leer la cámara unos segundos
                estado["pausa_hasta"] = time.time() + float(b.get("segundos", 15))
                return jsonify(ok=True)
            if acc == "registrar":
                if b.get("modo") == "quieto":   # registro estático (App 961+): frente + lados, termina solo (máx. 20 s)
                    cuerpo = {"name": b["nombre"], "still": True}
                    if b.get("agregar"):
                        cuerpo["add"] = True   # foto 2 y 3: se suman como huellas propias, no reemplazan la 1
                    r = cam(ip, "POST", "faces/enroll", json=cuerpo)
                    if r.get("success") is not False:
                        with db() as con:
                            en_curso[ip] = con.execute(
                                "INSERT INTO registros (camara, nombre, modo, t_ini) VALUES (?,?,?,?)",
                                (ip, b["nombre"], f"quieto-f{b.get('foto', 1)}", time.time())).lastrowid
                    return jsonify(r)
                cuerpo = {"name": b["nombre"], "walk": True, "distance": b.get("distancia", "cerca")}
                if b.get("agregar"):
                    cuerpo["add"] = True   # parche 0057: conserva sus fotos y suma las nuevas
                return jsonify(cam(ip, "POST", "faces/enroll", json=cuerpo))
            if acc == "cancelar":
                r = cam(ip, "POST", "faces/enroll/cancel")
                registro_fin(ip, {"active": False, "result": "cancelado (tardó)"})
                return jsonify(r)
            if acc == "ojos":
                crops = cam(ip, "GET", "faces/debug")["data"]["crops"]
                return jsonify(crops=[dict(seq=c["seq"], edad_ms=c["age_ms"], ojos=c["eye_px"], resultado=c["result"],
                                           persona=c["best"], score=c["score"]) for c in crops])
            if acc == "activos":      # {"solo": [ids]} o {"todos": true}
                cuerpo = {"all": True} if b.get("todos") else {"only": b.get("solo", [])}
                return jsonify(cam(ip, "POST", "faces/active", json=cuerpo))
            if acc == "borrar_banco":
                d = cam(ip, "GET", "faces")["data"]
                ids = [p["id"] for p in d["people"] if PREFIJO.match(p["name"])]
                for pid in ids:
                    cam(ip, "POST", "faces/delete", json={"id": pid})
                return jsonify(borradas=len(ids))
        except Exception as e:  # noqa: BLE001
            return jsonify(error=str(e)), 502
        return jsonify(error="acción desconocida"), 400

    @app.route("/api/banco/pasada", methods=["POST"])
    def api_pasada():
        b = request.json or {}
        ahora = time.time()
        with db() as con:
            if b.get("fase") == "inicio":
                cur = con.execute("INSERT INTO pasadas (corrida, camara, verdad, lfw, t_ini) VALUES (?,?,?,?,?)",
                                  (b["corrida"], b["ip"], b["verdad"], b.get("lfw"), ahora))
                estado["prueba"] = f"{b['corrida']}|{b['verdad']}"
                return jsonify(id=cur.lastrowid)
            if b.get("fase") == "descartar":   # la cámara no vio la cara: la pasada no cuenta y se repite
                con.execute("DELETE FROM pasadas WHERE id=?", (b["id"],))
                estado["prueba"] = ""
                return jsonify(ok=True)
            con.execute("UPDATE pasadas SET t_fin=? WHERE id=?", (ahora, b["id"]))
            estado["prueba"] = ""
        return jsonify(ok=True)

    @app.route("/api/banco/hechas")
    def api_hechas():
        """Pasadas terminadas de una corrida (la última si no se indica), para retomarla; borra las que quedaron a medias."""
        corrida = request.args.get("corrida", "")
        with db() as con:
            if not corrida:
                fila = con.execute("SELECT corrida FROM pasadas ORDER BY id DESC LIMIT 1").fetchone()
                corrida = fila[0] if fila else ""
            a_medias = con.execute("DELETE FROM pasadas WHERE corrida=? AND t_fin IS NULL", (corrida,)).rowcount
            hechas = [dict(verdad=r[0], lfw=r[1]) for r in
                      con.execute("SELECT verdad, lfw FROM pasadas WHERE corrida=? ORDER BY id", (corrida,))]
        return jsonify(corrida=corrida, hechas=hechas, a_medias=a_medias)

    @app.route("/api/banco/resultados")
    def api_resultados():
        corrida = request.args.get("corrida", "")
        with db() as con:
            if not corrida:
                fila = con.execute("SELECT corrida FROM pasadas ORDER BY id DESC LIMIT 1").fetchone()
                corrida = fila[0] if fila else ""
            pasadas = [dict(r) for r in con.execute("SELECT * FROM pasadas WHERE corrida=? AND t_fin IS NOT NULL ORDER BY id",
                                                    (corrida,))]
            filas = []
            for p in pasadas:
                ints = [dict(r) for r in con.execute(
                    "SELECT * FROM intentos WHERE camara=? AND ts BETWEEN ? AND ? AND resultado != 'registro' ORDER BY ts",
                    (p["camara"], p["t_ini"] - 0.2, p["t_fin"]))]   # t_fin ya incluye los 1.8 s sin cara; +1.5 se metía en la pasada siguiente
                nombre = next((i for i in ints if i["resultado"] == "reconocido"), None)
                if p["verdad"] == "nadie":
                    res = "falso_positivo" if nombre else "ok_desconocido"
                elif not nombre:
                    res = "sin_nombre"
                else:
                    res = "correcto" if nombre["persona"] == p["verdad"] else "equivocado"
                filas.append(dict(verdad=p["verdad"], lfw=p["lfw"], resultado=res, intentos=len(ints),
                                  nombre=nombre["persona"] if nombre else "", score=nombre["score"] if nombre else None,
                                  ojos=nombre["ojos"] if nombre else None,
                                  ms=round((nombre["ts"] - p["t_ini"]) * 1000) if nombre else None,
                                  mejor=max((i["score"] or 0 for i in ints), default=None)))
        reg = [f for f in filas if f["verdad"] != "nadie"]
        imp = [f for f in filas if f["verdad"] == "nadie"]

        def pct(lista, r):
            return round(100.0 * sum(1 for f in lista if f["resultado"] == r) / len(lista), 1) if lista else None

        def med(v):
            v = [x for x in v if x is not None]
            return round(statistics.median(v), 1) if v else None

        resumen = dict(corrida=corrida, pasadas=len(filas), registrados=len(reg), impostores=len(imp),
                       correcto=pct(reg, "correcto"), equivocado=pct(reg, "equivocado"), sin_nombre=pct(reg, "sin_nombre"),
                       falso_positivo=pct(imp, "falso_positivo"),
                       ojos_al_nombre=med(f["ojos"] for f in reg if f["resultado"] == "correcto"),
                       ms_al_nombre=med(f["ms"] for f in reg if f["resultado"] == "correcto"),
                       intentos_por_pasada=med(f["intentos"] for f in filas),
                       mejor_score_impostor=max((f["mejor"] or 0 for f in imp), default=None))
        with db() as con:
            corridas = [r[0] for r in con.execute("SELECT DISTINCT corrida FROM pasadas ORDER BY id DESC")]
        return jsonify(resumen=resumen, filas=filas, corridas=corridas)
