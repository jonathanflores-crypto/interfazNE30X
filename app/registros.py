"""Registros del día (27-nov): todo lo que guarda la base, en archivos por día, para abrir en Excel.

- data/registros/AAAA-MM-DD_intentos.csv y AAAA-MM-DD_eventos.csv: se llenan solos cada CADA_S segundos con lo
  nuevo de la base (hora local de ZONA). La primera vez se escriben también los días que ya estaban en la base.
  UTF-8 con BOM para que Excel lea bien los acentos. Sin embeddings ni el JSON crudo del evento.
- GET /registros/<fecha>.xlsx: Excel del día con dos hojas (Intentos y Eventos), leído directo de la base.
- GET /registros/<fecha>_<intentos|eventos>.csv: el CSV del día tal como está en data/registros/.
- GET /api/registros?fecha=AAAA-MM-DD[&camara=ip]: los intentos de ese día (tabla «ver todo el día») y los días
  con datos.
"""
import csv
import datetime
import io
import json
import os
import re
import threading
import time
from zoneinfo import ZoneInfo

from flask import Response, abort, jsonify, request, send_from_directory

ZONA = ZoneInfo(os.environ.get("ZONA", "America/Mexico_City"))
CADA_S = 5          # cada cuánto se pasa lo nuevo de la base a los CSV
LOTE = 5000         # filas por vuelta (la primera vez hay que escribir días enteros)

# columna del archivo -> cómo sale de la fila de la base
INTENTOS = ["id", "fecha", "hora", "camara", "seq", "resultado", "persona", "puntaje", "segundo", "puntaje_2",
            "ventaja", "huella", "ojos_px", "deteccion", "umbral", "prueba", "foto", "arranque"]
EVENTOS = ["id", "fecha", "hora", "camara", "tipo", "persona", "puntaje", "tiempo_ms", "prueba"]


def local(ts):
    return datetime.datetime.fromtimestamp(ts, ZONA)


def r3(v):
    return round(v, 3) if isinstance(v, (int, float)) else v


def fila_intento(r):
    t = local(r["ts"])
    ventaja = r["score"] - (r["score2"] or 0) if r["score"] is not None else None
    return [r["id"], t.strftime("%Y-%m-%d"), t.strftime("%H:%M:%S"), r["camara"], r["seq"], r["resultado"],
            r["persona"], r3(r["score"]), r["segundo"], r3(r["score2"]), r3(ventaja), r["plantilla"],
            r3(r["ojos"]), r3(r["conf"]), r3(r["umbral"]), r["prueba"], r["foto"], r["arranque"]]


def fila_evento(r):
    t = local(r["ts"])
    return [r["id"], t.strftime("%Y-%m-%d"), t.strftime("%H:%M:%S"), r["camara"], r["tipo"], r["persona"],
            r3(r["score"]), r["tiempo_ms"], r["prueba"]]


TABLAS = {"intentos": (INTENTOS, fila_intento), "eventos": (EVENTOS, fila_evento)}


def limites_dia(fecha):
    """ts unix de inicio y fin de un día local (AAAA-MM-DD)."""
    d = datetime.datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=ZONA)
    return d.timestamp(), (d + datetime.timedelta(days=1)).timestamp()


def instalar(app, db, datos_dir):
    carpeta = os.path.join(datos_dir, "registros")
    os.makedirs(carpeta, exist_ok=True)
    marca = os.path.join(carpeta, ".ultimo.json")   # último id de cada tabla ya pasado a los CSV

    def leer_marca():
        try:
            with open(marca) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def pasar_nuevos():
        ultimo = leer_marca()
        for tabla, (cols, fila) in TABLAS.items():
            while True:
                with db() as con:
                    filas = con.execute(f"SELECT * FROM {tabla} WHERE id > ? ORDER BY id LIMIT ?",
                                        (ultimo.get(tabla, 0), LOTE)).fetchall()
                if not filas:
                    break
                por_dia = {}
                for r in filas:
                    f = fila(r)
                    por_dia.setdefault(f[1], []).append(f)
                for dia, fs in por_dia.items():
                    archivo = os.path.join(carpeta, f"{dia}_{tabla}.csv")
                    nuevo = not os.path.exists(archivo)
                    with open(archivo, "a", newline="", encoding="utf-8-sig" if nuevo else "utf-8") as fh:
                        w = csv.writer(fh)
                        if nuevo:
                            w.writerow(cols)
                        w.writerows(fs)
                ultimo[tabla] = filas[-1]["id"]
                with open(marca + ".tmp", "w") as fh:     # la marca se cambia de un golpe: nunca a medias
                    json.dump(ultimo, fh)
                os.replace(marca + ".tmp", marca)

    def hilo():
        while True:
            try:
                pasar_nuevos()
            except Exception as e:  # noqa: BLE001  (se reintenta en la siguiente vuelta)
                print("registros: no se pudo escribir el CSV:", e)
            time.sleep(CADA_S)

    threading.Thread(target=hilo, daemon=True).start()

    def fecha_pedida():
        fecha = request.args.get("fecha") or local(time.time()).strftime("%Y-%m-%d")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            abort(400)
        return fecha

    @app.route("/api/registros")
    def api_registros():
        fecha = fecha_pedida()
        cam = request.args.get("camara", "")
        ini, fin = limites_dia(fecha)
        with db() as con:
            filas = [dict(r) for r in con.execute(
                "SELECT id, ts, camara, seq, resultado, persona, score, segundo, score2, plantilla, ojos, conf, "
                "umbral, prueba, foto, arranque FROM intentos WHERE ts >= ? AND ts < ? AND (?='' OR camara=?) "
                "ORDER BY id DESC", (ini, fin, cam, cam))]
        dias = sorted({n[:10] for n in os.listdir(carpeta) if re.match(r"\d{4}-\d{2}-\d{2}_", n)}, reverse=True)
        return jsonify(fecha=fecha, intentos=filas, dias=dias)

    @app.route("/registros/<nombre>.csv")
    def registro_csv(nombre):
        m = re.fullmatch(r"\d{4}-\d{2}-\d{2}_(intentos|eventos)", nombre)
        if not m:
            abort(404)
        if not os.path.exists(os.path.join(carpeta, nombre + ".csv")):   # día sin registros: solo los encabezados
            return Response("﻿" + ",".join(TABLAS[m.group(1)][0]) + "\r\n", mimetype="text/csv",
                            headers={"Content-Disposition": f"attachment; filename={nombre}.csv"})
        return send_from_directory(carpeta, nombre + ".csv", as_attachment=True, mimetype="text/csv")

    @app.route("/registros/<fecha>.xlsx")
    def registro_xlsx(fecha):
        from openpyxl import Workbook
        from openpyxl.styles import Font

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            abort(404)
        ini, fin = limites_dia(fecha)
        libro = Workbook()
        libro.remove(libro.active)
        with db() as con:
            for tabla, (cols, fila) in TABLAS.items():
                hoja = libro.create_sheet(tabla.capitalize())
                hoja.append(cols)
                for r in con.execute(f"SELECT * FROM {tabla} WHERE ts >= ? AND ts < ? ORDER BY id", (ini, fin)):
                    hoja.append(fila(r))
                for c in hoja[1]:
                    c.font = Font(bold=True)
                hoja.freeze_panes = "A2"
                hoja.auto_filter.ref = hoja.dimensions
                for i, col in enumerate(cols, 1):
                    hoja.column_dimensions[hoja.cell(1, i).column_letter].width = max(9, len(col) + 3)
        buf = io.BytesIO()
        libro.save(buf)
        return Response(buf.getvalue(),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=registros_{fecha}.xlsx"})
