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
```powershell
.\scripts\levantar.ps1
```
- La primera vez crea el archivo `.env` y se detiene. Ábrelo (`notepad .env`), pon las IPs de las cámaras
  (`CAMARAS=192.168.x.x,192.168.x.y`) y su contraseña (`CAM_PASS=`), guarda y vuelve a correr
  `.\scripts\levantar.ps1`.
- Al terminar muestra la dirección del broker (`IP:1883`) que se configura en las cámaras.
- Si PowerShell dice que no se pueden ejecutar scripts, corre una vez:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```
- Si el **Firewall de Windows** pregunta, permite Docker en redes **privadas** (puertos 8095 y 1883); si no,
  las cámaras no llegan al broker.

### 4. Actualizar
```powershell
.\scripts\actualizar.ps1
```

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
- La primera vez crea el archivo `.env` y se detiene. Ábrelo (`open -e .env`), pon las IPs de las cámaras
  (`CAMARAS=192.168.x.x,192.168.x.y`) y su contraseña (`CAM_PASS=`), guarda y vuelve a correr
  `./scripts/levantar.sh`.
- Al terminar muestra la dirección del broker (`IP:1883`) que se configura en las cámaras.
- Si macOS pregunta si Docker puede aceptar conexiones entrantes, permítelo.

### 4. Actualizar
```bash
./scripts/actualizar.sh
```

---

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
