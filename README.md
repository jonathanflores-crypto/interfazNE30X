# Interfaz NE30X

Interfaz de laboratorio para las cámaras **NE301 / NE302** con reconocimiento facial (firmware 976).
Recibe los eventos por MQTT (reconocidos, desconocidos, estado de la cámara y **guía del registro**), lee los
intentos de la cámara, registra y borra personas, baja testigos y muestra el control de calidad del registro.

Todo corre en Docker (monitor + broker MQTT Mosquitto), así que funciona igual en **Windows** y **Mac**.

| Servicio | Dirección |
|---|---|
| Interfaz web | http://localhost:8095 |
| Broker MQTT (para las cámaras) | IP de esta PC, puerto 1883 |

La PC y las cámaras deben estar en la misma red. La primera vez empieza **vacía**: la base, las fotos y los
testigos se crean en `data/`, que no se sube a git.

---

## Windows

### 1. Instalar
- **Docker Desktop**: https://www.docker.com/products/docker-desktop/ (al instalar, deja activado WSL 2).
  Ábrelo y espera a que diga *Engine running*.
- **Git**: https://git-scm.com/download/win

### 2. Bajar el proyecto
En **PowerShell**:
```powershell
cd $HOME\Documents
git clone https://github.com/jonathanflores-crypto/interfazNE30X.git
cd interfazNE30X
```

### 3. Levantar
Doble clic en **`levantar.bat`** (en la carpeta `interfazNE30X`), o desde la terminal:
```powershell
.\levantar.bat
```
(en PowerShell lleva `.\` adelante; en CMD basta `levantar.bat`)
> No abras los `.ps1` con doble clic ni los corras desde CMD: Windows los abre como texto en el Bloc de notas.
> Los `.bat` llaman a PowerShell por ti y no necesitan cambiar la política de scripts.
- La primera vez crea el archivo `.env` y se detiene. Para saber las IPs de las cámaras corre
  `buscar_camaras.bat` (ver [Buscar las cámaras](#buscar-las-cámaras-en-la-red)). Abre el `.env` (`notepad .env`), pon las IPs de las cámaras
  (`CAMARAS=192.168.x.x,192.168.x.y`) y su contraseña (`CAM_PASS=`), guarda y vuelve a correr
  `.\levantar.bat`.
- Al terminar muestra un recuadro con la IP de esta PC: la dirección de la interfaz (`http://IP:8095`, también
  desde otro equipo) y la del broker MQTT (`IP` puerto `1883`) que se configura en las cámaras. La misma IP sale
  arriba en la página.
- Si el **Firewall de Windows** pregunta, permite Docker en redes **privadas** (puertos 8095 y 1883); si no,
  las cámaras no llegan al broker.

### 4. Actualizar
Doble clic en **`actualizar.bat`**, o desde la terminal: `.\actualizar.bat`

---

## Mac

### 1. Instalar
- **Docker Desktop**: https://www.docker.com/products/docker-desktop/ (elige Apple Silicon o Intel según tu Mac).
  Ábrelo y espera a que diga *Engine running*.
- **Git**: ya viene con las herramientas de desarrollo; si `git --version` no responde, corre
  `xcode-select --install`.

### 2. Bajar el proyecto
En **Terminal**:
```bash
cd ~/Documents
git clone https://github.com/jonathanflores-crypto/interfazNE30X.git
cd interfazNE30X
```

### 3. Levantar
```bash
./scripts/levantar.sh
```
- La primera vez crea el archivo `.env` y se detiene. Para saber las IPs de las cámaras corre
  `./scripts/buscar_camaras.sh` (ver [Buscar las cámaras](#buscar-las-cámaras-en-la-red)). Abre el `.env` (`open -e .env`), pon las IPs de las cámaras
  (`CAMARAS=192.168.x.x,192.168.x.y`) y su contraseña (`CAM_PASS=`), guarda y vuelve a correr
  `./scripts/levantar.sh`.
- Al terminar muestra un recuadro con la IP de esta PC: la dirección de la interfaz (`http://IP:8095`, también
  desde otro equipo) y la del broker MQTT (`IP` puerto `1883`) que se configura en las cámaras. La misma IP sale
  arriba en la página.
- Si macOS pregunta si Docker puede aceptar conexiones entrantes, permítelo.

### 4. Actualizar
```bash
./scripts/actualizar.sh
```

---

## Buscar las cámaras en la red
Las cámaras toman IP por DHCP, así que cambia en cada red. El buscador recorre la red de la PC y encuentra las
cámaras CamThink por su MAC (`44:9f:da` o `04:ec:a9`): muestra IP, web y RTSP de cada una. Esas IPs van en
`CAMARAS=` del `.env`.

| | Comando |
|---|---|
| Windows | doble clic en `buscar_camaras.bat` (con varias redes: `.\buscar_camaras.bat -Interfaz "Wi-Fi"`) |
| Mac | `./scripts/buscar_camaras.sh` (por cable u otra interfaz: `./scripts/buscar_camaras.sh en1`) |

Si no aparece ninguna: la cámara debe estar prendida y en la misma red. Una cámara nueva o sin WiFi configurado
tiene su propia red (`NE30x_XXXXXX`): conéctate a ella y entra a http://192.168.10.10.

## Apuntar las cámaras al broker
En la web de cada cámara, sección MQTT:
- **Servidor**: la IP de esta PC (la que muestra el script al levantar).
- **Puerto**: 1883, sin usuario ni contraseña.
- **client_id**: uno distinto por cámara (por ejemplo `ne301-18`, `ne302-33`).
- Guardar y **conectar**.

Si se configura por la API, hay que mandar los cuatro datos juntos (servidor, puerto, client_id y conectar):
con solo servidor y puerto el `client_id` queda vacío y la cámara no conecta.

En la interfaz, la cámara aparece en *Versiones* y los eventos empiezan a llegar en unos segundos.

## Actualizar
`actualizar` baja lo último del repo (`git pull`) y reconstruye el contenedor. **No toca** `data/` ni `.env`:
las personas registradas, fotos y configuración se conservan.

## Presets de configuración
La cámara no guarda presets: solo los valores que recibe. Los presets viven en `app/presets.json` (Preset 1 Escuela
—probado—, Preset 2 Oficina Novus, Preset 3 Acceso de cerca, Preset 4 Pasillo y Preset 5 Poca luz / contraluz). En la página, sección
*Configuración de la cámara*: elige el preset y **Aplicar a la cámara**; la interfaz manda los valores, los vuelve a
leer y dice qué preset tiene la cámara (o «personalizado» si alguien la cambió a mano). Para cambiar valores,
edita `app/presets.json` y corre `actualizar`.

## Modelo del control de calidad (no va en el repo)
El control de calidad del registro usa `app/modelos/mfn_peleab_int8.onnx`, que **no se publica** (licencia)
y se entrega aparte. Cópialo a `app/modelos/` y corre `actualizar`. Sin él todo lo demás funciona; en el log
aparece `calidad del registro desactivada`.

## Comandos útiles
| Qué | Comando (igual en Windows y Mac) |
|---|---|
| Ver si está corriendo | `docker compose ps` |
| Ver el log del monitor | `docker compose logs -f monitor` |
| Ver el log del broker | `docker compose logs -f mqtt` |
| Detener | `docker compose down` |
| Volver a levantar | `docker compose up -d` |

## Problemas comunes
- **`port is already allocated`**: otro programa ya usa el 8095 o el 1883 (otro Mosquitto u otro monitor).
  Detenlo o cambia el puerto de la izquierda en `docker-compose.yml` (por ejemplo `"8097:8000"`).
- **La cámara no aparece / no llegan eventos**: revisa que la cámara apunte a la IP correcta de la PC, que
  esté en la misma red y que el firewall deje pasar el puerto 1883.
- **`docker: command not found`** o el script no avanza: abre Docker Desktop y espera a *Engine running*.
