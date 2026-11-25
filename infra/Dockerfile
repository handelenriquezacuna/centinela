# Imagen del servicio `app`: la API de Centinela corriendo DENTRO de Compose,
# la segunda modalidad de arranque (seccion 6 del plan, H-00B).
#
# El contexto de build es la RAIZ del repo, no `infra/`: `app/`, `config/`,
# `pyproject.toml` y `uv.lock` viven ahi, y este Dockerfile los necesita a todos.
# Por eso `infra/docker-compose.yml` declara `context: ..` y
# `dockerfile: infra/Dockerfile` para el servicio `app`.
#
# Dos etapas (T-67). La primera construye la SPA con Node; la segunda es la imagen que
# corre y no lleva Node: solo copia el resultado del build (`app/estaticos/spa/`), que
# FastAPI sirve como cualquier otro estatico. En ejecucion no hay npm ni red.

# --- Etapa 1: el build de la SPA ------------------------------------------------------
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

# --- Etapa 2: la imagen que corre ------------------------------------------------------
FROM python:3.13-slim

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
# La SPA construida en la etapa 1. `.dockerignore` deja afuera cualquier build del host,
# asi que la imagen siempre lleva el que sale de las fuentes y el lock versionados.
COPY --from=spa /src/app/estaticos/spa ./app/estaticos/spa

RUN uv sync --locked --no-dev

# Usuario sin privilegios (T-10). Los archivos quedan de root y solo lectura para el
# proceso: la app no escribe en su propia carpeta. La cache de uv va a /tmp porque el
# usuario no tiene HOME escribible.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin centinela
ENV UV_CACHE_DIR=/tmp/uv-cache
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
