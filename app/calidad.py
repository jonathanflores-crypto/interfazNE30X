"""Control de calidad del registro (23-nov): una persona nueva debe quedar separada de las demás.

Tres revisiones por persona (se recalculan solas cada 15 s):
- A. Contra los registrados: sus huellas (cerca/lejos) y fotos, leídas de /faces/backup, contra las de cada
  otra persona de la cámara. Entre registros buenos el par más parecido de la .18 fue 0.58 (N_04/N_12).
- B. Contra los dummies: las fotos de intentos que el servidor ya guardó de esta cámara (gente que pasó y no
  es esta persona). Su embedding se saca aquí con el mismo MobileFaceNet (coincide con la cámara ~0.95).
- C. Pasada de verificación: en los 5 min después de registrarse, la persona pasa; vale si la cámara le da
  >= 0.65 y 0.15 sobre el 2.º lugar (después ya no: el nombre de un intento puede ser otra persona). Los registros malos de N_01_Qui daban 0.30-0.36 contra su propia cara.
"""
import json
import os
import re
import threading
import time

import numpy as np
import requests
from PIL import Image

A_ROJO, A_AMARILLO = 0.62, 0.57      # parecido con otro registrado
B_AMARILLO = 0.62                    # parecido con un dummy (puede ser la misma persona: se enseña la foto)
C_SCORE, C_MARGEN, C_FALLA = 0.65, 0.15, 8   # pasada buena; tras C_FALLA intentos suyos sin una buena: débil
VENTANA_REGISTRO_S = 600             # intentos de los 10 min antes del registro: es la persona registrándose
VERIFICACION_S = 300                 # y de los 5 min después: su pasada de verificación
MODELO = os.path.join(os.path.dirname(__file__), "modelos", "mfn_peleab_int8.onnx")


def instalar(app, db, estado, auth, camaras, datos_dir):
    if not os.path.exists(MODELO):
        # El modelo no va en el repo: copiarlo a app/modelos/ (ver LEEME). Sin él, todo lo demás funciona.
        print("calidad del registro desactivada: falta", MODELO)
        return None
    cache_dir = os.path.join(datos_dir, "calidad")
    resultados = {}          # (ip, pid) -> dict
    sesion = {}

    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS fotos_emb (camara TEXT, arranque INTEGER, seq INTEGER, emb TEXT, "
                    "PRIMARY KEY (camara, arranque, seq))")

    def unit(v):
        v = np.asarray(v, dtype=np.float32)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def embedding(archivo):
        """Mismo preproceso que la cámara con norm=true (contraste normalizado) + (x-127.5)/128."""
        if "s" not in sesion:
            import onnxruntime as ort
            sesion["s"] = ort.InferenceSession(MODELO, providers=["CPUExecutionProvider"])
        a = np.asarray(Image.open(archivo).convert("RGB").resize((112, 112)), dtype=np.float32)
        a = (a - a.mean()) / (a.std() + 1e-6) * 64 + 127.5
        x = ((a - 127.5) / 128.0).transpose(2, 0, 1)[None].copy()
        return unit(sesion["s"].run(None, {"input": x})[0][0])

    def embeber_intentos(ip, lote=60):
        """Dummies: saca el embedding de las fotos de intentos ya guardadas que aún no lo tienen."""
        with db() as con:
            filas = con.execute(
                "SELECT i.arranque, i.seq FROM intentos i LEFT JOIN fotos_emb f ON f.camara=i.camara AND "
                "f.arranque=i.arranque AND f.seq=i.seq WHERE i.camara=? AND i.foto='ok' AND f.seq IS NULL "
                "ORDER BY i.id DESC LIMIT ?", (ip, lote)).fetchall()
        for a, s in filas:
            archivo = os.path.join(datos_dir, "fotos", ip, f"{a}_{s}.bmp")
            try:
                e = json.dumps([round(float(x), 5) for x in embedding(archivo)])
            except (OSError, ValueError):
                e = None
            with db() as con:
                con.execute("INSERT OR REPLACE INTO fotos_emb VALUES (?,?,?,?)", (ip, a, s, e))

    def respaldo(ip, p):
        """Huellas + fotos de una persona en el espacio de la cámara (cacheado por id y fecha de registro)."""
        archivo = os.path.join(cache_dir, ip, f"{p['id']}_{p.get('created')}.json")
        if not os.path.exists(archivo):
            r = requests.get(f"http://{ip}/api/v1/faces/backup", params={"id": p["id"]}, auth=auth, timeout=20)
            d = r.json()["data"]
            vec = [dict(k=k_tpl, v=t) for k_tpl, t in zip(("cerca", "lejos"), d.get("templates", []))
                   if d.get("tpl_mask", 0) >> ("cerca", "lejos").index(k_tpl) & 1 and any(t)]
            vec += [dict(k=f"foto {i + 1}", v=f["embedding"]) for i, f in enumerate(d.get("fotos", []))
                    if f.get("embedding")]
            os.makedirs(os.path.dirname(archivo), exist_ok=True)
            with open(archivo, "w") as fh:
                json.dump(vec, fh)
        with open(archivo) as fh:
            vec = json.load(fh)
        return [x["k"] for x in vec], np.array([unit(x["v"]) for x in vec]) if vec else np.zeros((0, 128))

    def revisar(ip, p, todos):
        claves, R = todos[p["id"]]
        avisos, estado_c = [], None
        if not len(R):
            return dict(estado="pendiente", texto="sin huellas todavía", avisos=[])
        # A. contra los demás registrados
        peor = (0.0, "", "", "")
        for q in todos:
            if q == p["id"] or not len(todos[q][1]):
                continue
            m = R @ todos[q][1].T
            i, j = np.unravel_index(int(m.argmax()), m.shape)
            if m[i, j] > peor[0]:
                peor = (float(m[i, j]), nombres[q], claves[i], todos[q][0][j])
        nivel_a = "rojo" if peor[0] >= A_ROJO else ("amarillo" if peor[0] >= A_AMARILLO else "")
        if nivel_a:
            avisos.append(dict(nivel=nivel_a, texto=f"se parece {peor[0]:.2f} a {peor[1]} ({peor[2]} vs {peor[3]})"))
        # B. contra los dummies (fotos de intentos de otra gente en esta cámara)
        creado = p.get("created") or 0
        with db() as con:
            filas = con.execute(
                "SELECT f.arranque, f.seq, f.emb FROM fotos_emb f JOIN intentos i ON i.camara=f.camara AND "
                "i.arranque=f.arranque AND i.seq=f.seq WHERE f.camara=? AND f.emb IS NOT NULL AND "
                "COALESCE(i.persona,'') != ? AND NOT (i.ts BETWEEN ? AND ?)",
                (ip, p["name"], creado - VENTANA_REGISTRO_S, creado + VERIFICACION_S)).fetchall()
        if filas:
            D = np.array([json.loads(e) for _, _, e in filas], dtype=np.float32)
            s = (D @ R.T).max(1)
            k = int(s.argmax())
            if s[k] >= B_AMARILLO:
                avisos.append(dict(nivel="amarillo", texto=f"un dummy (intento #{filas[k][1]}) se parece {s[k]:.2f}: "
                                   "si es la misma persona no pasa nada", foto=f"/foto/{ip}/{filas[k][0]}/{filas[k][1]}"))
        # C. pasada de verificación (puntajes de la propia cámara, después del registro)
        with db() as con:
            suyos = con.execute("SELECT score, score2 FROM intentos WHERE camara=? AND persona=? AND ts BETWEEN ? AND ? "
                                "AND resultado != 'registro' ORDER BY id", (ip, p["name"], creado, creado + VERIFICACION_S)).fetchall()
        buenos = [s for s, s2 in suyos if (s or 0) >= C_SCORE and (s or 0) - (s2 or 0) >= C_MARGEN]
        mejor = max((s or 0 for s, _ in suyos), default=None)
        if buenos:
            estado_c, texto = "ok", f"pasada de verificación buena ({max(buenos):.2f})"
        elif len(suyos) >= C_FALLA or (suyos and time.time() > creado + VERIFICACION_S):
            estado_c, texto = "falla", f"en su pasada de verificación no llegó: mejor {mejor:.2f} en {len(suyos)} intentos"
        elif time.time() > creado + VERIFICACION_S:
            estado_c, texto = "sin", "sin pasada de verificación (se hace en los 5 min después de registrar)"
        else:
            texto = "falta la pasada de verificación: que pase frente a la cámara" + (
                f" (mejor hasta ahora {mejor:.2f})" if suyos else "")
        if nivel_a == "rojo" or estado_c == "falla":
            est = "debil"
        elif estado_c == "ok":
            est = "revisar" if avisos else "bueno"
        elif estado_c == "sin":
            est = "sin_verificar"
        else:
            est = "pendiente"
        return dict(estado=est, texto=texto, avisos=avisos, a=round(peor[0], 3), mejor=mejor)

    nombres = {}

    def ciclo(ip):
        info = estado["camaras"].get(ip, {})
        people = [p for p in info.get("people", []) if not re.match(r"^L\d{3}_", p.get("name", ""))]
        if not info.get("ok") or not people:
            return
        embeber_intentos(ip)
        todos = {}
        for p in people:
            nombres[p["id"]] = p["name"]
            try:
                todos[p["id"]] = respaldo(ip, p)
            except (requests.RequestException, ValueError, KeyError):
                todos[p["id"]] = ([], np.zeros((0, 128)))
        for p in people:
            resultados[(ip, p["id"])] = revisar(ip, p, todos)

    def hilo():
        while True:
            for ip in list(camaras()):
                if time.time() < estado.get("pausa_hasta", 0):
                    continue
                try:
                    ciclo(ip)
                except Exception as e:  # noqa: BLE001  (se reintenta en la siguiente vuelta)
                    estado.setdefault("calidad_error", {})[ip] = f"{e.__class__.__name__}: {e}"
            time.sleep(15)

    threading.Thread(target=hilo, daemon=True).start()
    return lambda ip, pid: resultados.get((ip, pid))
