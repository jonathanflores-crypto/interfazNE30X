# Interfaz NE30X

Interfaz de laboratorio para las cámaras NE301 / NE302 con reconocimiento facial (firmware 976): recibe los
eventos por MQTT (reconocidos, desconocidos, estado, **guía del registro**), lee los intentos de la cámara,
registra y borra personas, baja testigos y muestra el control de calidad del registro.

Funciona igual en **Windows y Mac**: todo corre en Docker (monitor + broker MQTT Mosquitto).

## Requisitos
- Docker Desktop (Windows o Mac) y Git.
- La PC y las cámaras en la misma red.

## Primera vez
```bash
git clone https://github.com/jonathanflores-crypto/interfazNE30X.git
cd interfazNE30X
./scripts/levantar.sh          # Mac
# Windows (PowerShell):  .\scripts\levantar.ps1
```
La primera corrida crea `.env`: pon ahí las IPs de las cámaras (`CAMARAS=192.168.x.x,192.168.x.y`) y su
contraseña (`CAM_PASS`), y vuelve a correr el script. Abre **http://localhost:8095**.

Empieza vacío: la base, las fotos y los testigos se crean en `data/` (no se sube a git).

> Windows: si PowerShell no deja correr scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
> Si el firewall pregunta, permite Docker en redes privadas (puertos 8095 y 1883).

## Apuntar las cámaras al broker
En la web de cada cámara (MQTT): servidor = **IP de esta PC**, puerto **1883**, sin usuario, `client_id`
distinto por cámara, y conectar. Por la API hay que mandar los 4 campos juntos (servidor, puerto, client_id,
conectar): si solo se manda servidor y puerto, el `client_id` queda vacío y no conecta.

## Actualizar
```bash
./scripts/actualizar.sh        # Mac
# Windows:  .\scripts\actualizar.ps1
```
Baja lo último del repo y reconstruye; `data/` y `.env` no se tocan.

## Modelo del control de calidad (no va en el repo)
El control de calidad del registro usa `app/modelos/mfn_peleab_int8.onnx`, que **no se publica** (licencia).
Se entrega aparte; cópialo a `app/modelos/` y corre `actualizar`. Sin él todo lo demás funciona y el log dice
`calidad del registro desactivada`.

## Puertos
| Servicio | Puerto |
|---|---|
| Interfaz web | 8095 |
| Broker MQTT | 1883 |
