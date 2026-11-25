# Imagenes de Centinela. Un solo Dockerfile con cuatro etapas y dos destinos (`target`):
#
#   web  nginx sin privilegios: sirve la SPA construida y reenvia /api, /estado y /salud
#        a la API (D-73). No lleva Python, ni Node, ni el driver de Mongo.
#   api  la API de Python (uvicorn app.main:app) SIN la SPA, con mongodump y mongorestore
#        para los respaldos (D-74). No lleva Node ni nginx.
#
# Por que la API no lleva la SPA: la SPA se sirve completa desde `web`, asi que copiarla
# a la API solo sumaria peso y una segunda copia que mantener. Lo unico que se pierde es
# que las rutas de pagina ya no pasan por el guardia de la API: la pagina es el mismo
# `index.html` sin datos (igual que /estaticos, publico por construccion) y la sesion y los
# permisos se siguen decidiendo en cada endpoint de la API y en `GET /api/v1/sesion`, que
# es lo que la SPA consulta para mostrar o negar una pantalla.
#
# El contexto de build es la RAIZ del repo, no `infra/`: `app/`, `config/`,
# `pyproject.toml` y `uv.lock` viven ahi, y este Dockerfile los necesita a todos.
# Por eso `infra/docker-compose.yml` declara `context: ..` y
# `dockerfile: infra/Dockerfile`, con `target: web` o `target: api`.
#
# Dos etapas de apoyo: `spa` construye la SPA con Node y `herramientas` baja las
# herramientas de respaldo. En ejecucion no hay npm ni red.

# --- Etapa spa: el build de la SPA ------------------------------------------------------
# La version es la de frontend/.nvmrc (26.8.1), la misma del equipo y del CI; con
# `engine-strict` (frontend/.npmrc) otra version haria fallar `npm ci`. Si cambia el
# .nvmrc, cambia esta linea.
FROM node:26.8.1-slim AS spa

WORKDIR /src/frontend

# El lock primero: mientras no cambie, Docker reusa la capa de `npm ci`.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc frontend/.nvmrc ./
RUN npm ci --no-audit

# El build escribe en ../app/estaticos/spa (vite.config.ts), es decir /src/app/estaticos/spa.
COPY frontend/ ./
RUN npm run build

# --- Etapa herramientas: mongodump y mongorestore (D-74) ---------------------------------
# MongoDB Database Tools 100.20.1, el binario oficial de MongoDB verificado por sha256
# (los valores salen del manifiesto de descargas de MongoDB, downloads.mongodb.org/tools/db/
# release.json). Se baja con Python de la propia imagen (sin curl ni apt) y de todo el
# paquete solo se conservan los dos binarios. Los de Ubuntu 24.04 son ejecutables Go que
# enlazan dinamicamente glibc y libgssapi_krb5 (autenticacion Kerberos del driver): la
# segunda la instala la etapa `api`. Si cambia la version, cambian la VERSION y los dos sha256.
FROM python:3.13-slim AS herramientas
ARG TARGETARCH
ARG VERSION_HERRAMIENTAS=100.20.1
ARG SHA256_AMD64=20f43dcaa37f5a4841ac5a91da685a488a5ebbd81113c018e25a6b544cc350ea
ARG SHA256_ARM64=7644e12e351bceea542921e60769e3ed658fc770dc09eed07bc5cc2a1ca2ca41
RUN set -eu; \
    case "$TARGETARCH" in \
        amd64) arquitectura=x86_64; sha256="$SHA256_AMD64" ;; \
        arm64) arquitectura=arm64;  sha256="$SHA256_ARM64" ;; \
        *) echo "arquitectura no soportada: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    nombre="mongodb-database-tools-ubuntu2404-${arquitectura}-${VERSION_HERRAMIENTAS}"; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "https://fastdl.mongodb.org/tools/db/${nombre}.tgz" /tmp/herramientas.tgz; \
    echo "${sha256}  /tmp/herramientas.tgz" | sha256sum -c -; \
    mkdir /out; \
    tar -xzf /tmp/herramientas.tgz -C /out --strip-components=2 \
        "${nombre}/bin/mongodump" "${nombre}/bin/mongorestore"

# --- Destino web: nginx sin privilegios (D-73) -------------------------------------------
# Imagen oficial sin privilegios (usuario nginx, uid 101, escucha en 8080), fijada por el
# tag exacto de la version Y por el digest del indice multi-arquitectura: el tag documenta
# y el digest garantiza. Alpine: la imagen queda en pocos MB.
FROM nginxinc/nginx-unprivileged:1.29.8-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6 AS web

# Los estaticos compartidos (centinela.css, ...) y, encima, la SPA construida. `.dockerignore`
# deja afuera cualquier build del host: la imagen lleva siempre el de las fuentes y el lock.
COPY app/estaticos/ /usr/share/nginx/html/estaticos/
COPY --from=spa /src/app/estaticos/spa/ /usr/share/nginx/html/estaticos/spa/
COPY infra/nginx/cabeceras.conf infra/nginx/proxy-api.conf /etc/nginx/centinela/
COPY infra/nginx/centinela.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

# La salud de nginx, no la de la API (`/salud`): web esta sano si nginx responde.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/nginx-salud"]

# --- Destino api: la API de Python ------------------------------------------------------
FROM python:3.13-slim AS api

# El binario de uv se copia de su propia imagen oficial: es mas rapido y mas
# reproducible que instalarlo con pip, y no exige una cadena de compilacion que la
# imagen slim no trae.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

WORKDIR /app

# Los manifiestos primero, y el codigo despues: mientras el codigo cambie y el lock
# no, Docker reusa la capa de dependencias en vez de reinstalarlas en cada build.
# README.md entra aqui y no mas abajo por una razon chica pero real: `pyproject.toml`
# declara `readme = "README.md"` y hatchling valida que exista al instalar el propio
# paquete `centinela`, incluso en modo editable.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY app ./app
COPY config ./config
# Sin la SPA: la sirve `web`. `.dockerignore` deja afuera `app/estaticos/spa/`.

# mongodump y mongorestore, para los respaldos de la API (D-74). Agregan unos 100 MB a la
# imagen (ver docs/05-infraestructura.md).
COPY --from=herramientas /out/mongodump /out/mongorestore /usr/local/bin/
# La unica libreria que las herramientas piden y la imagen slim no trae (Kerberos del
# driver; con sus dependencias suma unos 3 MB). Sin recomendados y con las listas de apt
# borradas en la misma capa. El `--version` falla el build si falta algo mas.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && mongodump --version && mongorestore --version

RUN uv sync --locked --no-dev

# Usuario sin privilegios (T-10). Los archivos quedan de root y solo lectura para el
# proceso: la app no escribe en su propia carpeta. La cache de uv va a /tmp porque el
# usuario no tiene HOME escribible.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin centinela
# Donde la API guarda los respaldos (`respaldos.directorio`, ver docker-compose.yml): el unico
# lugar donde el proceso escribe. Un volumen con nombre que se monta aqui hereda el dueno.
RUN mkdir /respaldos && chown centinela:centinela /respaldos
ENV UV_CACHE_DIR=/tmp/uv-cache \
    CENTINELA_RESPALDOS__DIRECTORIO=/respaldos
USER centinela

EXPOSE 8000

# `/salud` responde 200 mientras el proceso vive (el estado de Mongo va en el cuerpo).
# Sin curl en la imagen slim: Python de la propia imagen, solo biblioteca estandar.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=4)"]

# Un solo worker, sin recarga: la deteccion tiene que estar activa exactamente una
# vez (dos workers escuchando el mismo change stream duplicarian la alerta), y la
# recarga es una comodidad de desarrollo que no pertenece a una imagen que se
# reconstruye para cada cambio.
#
# `--host 0.0.0.0` esta fijo a proposito y no sale de `config/centinela.yml`: es un
# requisito de CUALQUIER contenedor (bindear solo en 127.0.0.1 hace inalcanzable el
# puerto publicado, aunque el publicado sea el de `mongo1`), no una decision de
# negocio ni una preferencia de entorno. `servidor.host` en el YAML sigue siendo
# 127.0.0.1 para la modalidad de host (`tareas.py dev`), donde si es lo correcto.
#
# `--no-sync`: el entorno ya quedo armado en la capa de arriba con `uv sync
# --locked --no-dev`. Sin esta bandera, `uv run` vuelve a resolver en cada arranque
# del contenedor, nota que faltan los paquetes de `dev` (ruff, pytest) que la imagen
# omitio a proposito, y los instala en caliente -descargando de la red cada vez que
# el contenedor arranca, que es exactamente lo que una imagen reproducible no deberia
# hacer-.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
