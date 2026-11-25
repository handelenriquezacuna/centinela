"""D-73: dos servidores, `web` (nginx con la SPA) y `api` (uvicorn), verificado desde los
archivos. No necesita Docker: lee `infra/docker-compose.yml`, `infra/Dockerfile` y la
configuracion de nginx.

Lo que fija:

- hay dos servicios, `web` y `api`, y `web` no tiene acceso a Mongo (ni credenciales, ni
  volumenes, ni dependencia de la base);
- cada uno tiene HEALTHCHECK y `restart: unless-stopped`;
- nginx entrega las rutas de pagina de la SPA con `index.html`, reenvia /api, /estado, /salud
  y /openapi.json a la API, y los canales SSE salen sin buffer, con un timeout alto y las
  cabeceras de SSE;
- las cabeceras de seguridad y una CSP sin CDN;
- imagenes fijadas: nginx por tag exacto y digest, herramientas de Mongo por sha256.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "infra" / "docker-compose.yml"
DOCKERFILE = RAIZ / "infra" / "Dockerfile"
NGINX = RAIZ / "infra" / "nginx"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def conf() -> str:
    return (NGINX / "centinela.conf").read_text(encoding="utf-8")


def _sin_comentarios(texto: str) -> str:
    return "\n".join(l for l in texto.splitlines() if not l.strip().startswith("#"))


def _etapas() -> dict[str, str]:
    """El Dockerfile por etapa (`FROM ... AS nombre`), sin comentarios."""
    texto = _sin_comentarios(DOCKERFILE.read_text(encoding="utf-8"))
    partes = re.split(r"(?m)^(?=FROM )", texto)
    etapas: dict[str, str] = {}
    for parte in partes:
        coincidencia = re.match(r"FROM \S+ AS (\w+)", parte)
        if coincidencia:
            etapas[coincidencia.group(1)] = parte
    return etapas


# ---------------------------------------------------------------------------
# Compose: dos servicios, web sin Mongo
# ---------------------------------------------------------------------------


def prueba_hay_dos_servicios_web_y_api_con_su_destino_de_build(compose: dict) -> None:
    servicios = compose["services"]

    assert "app" not in servicios, "el servicio unico `app` se separo en `api` y `web`"
    assert servicios["api"]["build"]["target"] == "api"
    assert servicios["web"]["build"]["target"] == "web"
    for servicio in ("api", "web"):
        assert servicios[servicio]["build"]["dockerfile"] == "infra/Dockerfile"
        assert servicios[servicio]["build"]["context"] == ".."


def prueba_web_no_tiene_acceso_a_mongo(compose: dict) -> None:
    """Sin URI, sin credenciales, sin volumenes y sin dependencia de la base.

    Comparte el espacio de red de mongo1 (lo necesita para alcanzar a `api` en 127.0.0.1 sin
    publicar la API), asi que "sin acceso" es por contenido: no hay con que autenticarse ni
    driver con que hablarle, y el healthcheck y la config de nginx solo conocen la API.
    """
    web = compose["services"]["web"]

    assert "environment" not in web, "web no recibe variables: ni URI ni claves"
    assert "volumes" not in web, "web no monta datos ni secretos"
    assert "env_file" not in web
    assert set(web["depends_on"]) == {"api"}, "web depende de la API, no de Mongo"
    assert web["depends_on"]["api"]["condition"] == "service_healthy"
    assert not re.search(r"(?i)mongo(?!1\b)|\buri\b|clave|secreto", yaml.safe_dump(web))


def prueba_la_imagen_web_no_lleva_python_ni_el_driver_ni_la_configuracion_de_la_app() -> None:
    web = _etapas()["web"]

    assert not re.search(r"(?im)^(COPY|ADD)\s+(--\S+\s+)*(pyproject|uv\.lock|app\s|config|app/\s)", web)
    assert "pymongo" not in web and "uv sync" not in web and "python" not in web.lower()
    # lo unico que copia de `app/` son los estaticos publicos y el build de la SPA
    copias = re.findall(r"(?m)^COPY\s+(?:--from=\w+\s+)?(\S+)", web)
    assert set(copias) <= {
        "app/estaticos/", "/src/app/estaticos/spa/", "infra/nginx/cabeceras.conf",
        "infra/nginx/centinela.conf",
    }, copias


def prueba_la_api_no_se_publica_y_el_portal_si_en_localhost(compose: dict) -> None:
    publicados = [str(p) for p in compose["services"]["mongo1"]["ports"]]

    assert "127.0.0.1:8000:8080" in publicados
    assert not [p for p in publicados if p.endswith(":8000")], "el 8000 interno es la API"
    for servicio in ("api", "web"):
        assert "ports" not in compose["services"][servicio]


def prueba_los_dos_se_reinician_solos_y_no_escriben_en_su_imagen(compose: dict) -> None:
    for servicio in ("api", "web"):
        definicion = compose["services"][servicio]
        assert definicion["restart"] == "unless-stopped", f"{servicio}: restart"
        assert definicion["read_only"] is True, f"{servicio}: sistema de archivos de solo lectura"
        assert definicion["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in definicion["security_opt"]


def prueba_la_api_guarda_los_respaldos_en_un_volumen_y_no_comparte_el_de_los_nodos(
    compose: dict,
) -> None:
    api = compose["services"]["api"]

    assert api["volumes"] == ["respaldos:/respaldos"]
    assert "respaldos" in compose["volumes"]
    assert "respaldos" in _etapas()["api"] and "CENTINELA_RESPALDOS__DIRECTORIO=/respaldos" in _etapas()["api"]


# ---------------------------------------------------------------------------
# Dockerfile: healthchecks, usuario, versiones fijas
# ---------------------------------------------------------------------------


def prueba_cada_destino_declara_su_healthcheck() -> None:
    etapas = _etapas()

    assert "HEALTHCHECK" in etapas["web"] and "nginx-salud" in etapas["web"]
    assert "HEALTHCHECK" in etapas["api"] and "/salud" in etapas["api"]
    # la de web es la de nginx: no se rompe si la API cae (la de la API es /salud)
    assert "8000" not in etapas["web"].split("HEALTHCHECK")[1]


def prueba_la_api_corre_sin_privilegios_y_sin_la_spa() -> None:
    api = _etapas()["api"]

    assert re.search(r"(?m)^USER centinela$", api)
    assert "--from=spa" not in api, "la SPA la sirve web, no la API"
    assert "uvicorn" in api and "app.main:app" in api
    assert "mongodump" in api and "mongorestore" in api


def prueba_web_usa_nginx_sin_privilegios_con_tag_exacto_y_digest() -> None:
    texto = DOCKERFILE.read_text(encoding="utf-8")

    from_web = re.search(r"(?m)^FROM (nginxinc/nginx-unprivileged:\S+) AS web", texto)
    assert from_web is not None, "web parte de la imagen sin privilegios"
    imagen = from_web.group(1)
    assert re.fullmatch(r"nginxinc/nginx-unprivileged:\d+\.\d+\.\d+-alpine@sha256:[0-9a-f]{64}", imagen)
    assert "EXPOSE 8080" in _etapas()["web"], "puerto sin privilegio"


def prueba_las_herramientas_de_mongo_van_con_version_y_sha256_fijos() -> None:
    herramientas = _etapas()["herramientas"]

    assert re.search(r"ARG VERSION_HERRAMIENTAS=\d+\.\d+\.\d+", herramientas)
    assert len(re.findall(r"ARG SHA256_\w+=[0-9a-f]{64}", herramientas)) == 2  # amd64 y arm64
    assert "sha256sum -c" in herramientas, "el hash se verifica, no solo se declara"
    assert ":latest" not in DOCKERFILE.read_text(encoding="utf-8")


def prueba_ningun_destino_corre_como_root() -> None:
    assert re.search(r"(?m)^USER centinela$", _etapas()["api"])
    assert "nginx-unprivileged" in DOCKERFILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# nginx: SSE, SPA, proxy, cabeceras
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    modificador: str  # "", "=", "^~", "~"
    ruta: str
    cuerpo: str


def _locations(conf: str) -> list[Location]:
    sin = _sin_comentarios(conf)
    encontradas: list[Location] = []
    for coincidencia in re.finditer(r"location\s+(=|\^~|~\*?)?\s*(\S+)\s*\{", sin):
        inicio = coincidencia.end()
        profundidad, i = 1, inicio
        while profundidad:
            profundidad += {"{": 1, "}": -1}.get(sin[i], 0)
            i += 1
        encontradas.append(Location(coincidencia.group(1) or "", coincidencia.group(2), sin[inicio : i - 1]))
    return encontradas


def _resolver(locations: list[Location], ruta: str) -> Location:
    """El algoritmo de nginx: exacta, prefijo mas largo (con ^~ corta), regex en orden, prefijo."""
    for loc in locations:
        if loc.modificador == "=" and loc.ruta == ruta:
            return loc
    prefijos = [l for l in locations if l.modificador in ("", "^~") and ruta.startswith(l.ruta)]
    mejor = max(prefijos, key=lambda l: len(l.ruta), default=None)
    if mejor is not None and mejor.modificador == "^~":
        return mejor
    for loc in locations:
        if loc.modificador.startswith("~") and re.search(loc.ruta, ruta):
            return loc
    assert mejor is not None
    return mejor


def prueba_las_rutas_de_pagina_de_la_spa_caen_en_el_fallback_a_index_html(conf: str) -> None:
    from app.api.rutas.spa import PAGINAS_SPA

    locations = _locations(conf)
    ejemplos = {"{alerta_id}": "ALR-001", "{cuenta}": "6033-9001"}
    for plantilla in PAGINAS_SPA:
        ruta = plantilla
        for marcador, valor in ejemplos.items():
            ruta = ruta.replace(marcador, valor)
        elegida = _resolver(locations, ruta)
        assert elegida.ruta == "/", f"{ruta} no llega al fallback de la SPA: cae en {elegida.ruta}"
        assert "try_files /estaticos/spa/index.html" in elegida.cuerpo
        assert 'Cache-Control "no-cache"' in elegida.cuerpo, "index.html no se guarda en cache"


def prueba_api_estado_salud_y_openapi_se_reenvian_a_la_api(conf: str) -> None:
    from app.seguridad.permisos import ACCESOS

    locations = _locations(conf)
    superficie = [
        a.ruta for a in ACCESOS if a.ruta.startswith(("/api/", "/estado", "/salud", "/openapi"))
    ]
    assert len(superficie) > 20
    for plantilla in superficie:
        ruta = re.sub(r"\{\w+\}", "X-1", plantilla)
        elegida = _resolver(locations, ruta)
        assert "proxy-api.conf" in elegida.cuerpo, f"{ruta} no se reenvia a la API ({elegida.ruta})"


def prueba_lo_que_no_es_de_la_spa_ni_de_la_api_no_se_reenvia_ni_se_sirve_como_pagina(conf: str) -> None:
    locations = _locations(conf)

    assert _resolver(locations, "/favicon.ico").ruta.startswith("\\.")  # el 404 por extension
    assert "return 404" in _resolver(locations, "/favicon.ico").cuerpo
    assert "return 404" not in _resolver(locations, "/api/v1/transacciones/exportar.csv").cuerpo, (
        "la exportacion CSV (que lleva extension) tiene que llegar a la API"
    )


@pytest.mark.parametrize("ruta", ["/api/v1/alertas/eventos", "/api/v1/alertas/flujo"])
def prueba_los_canales_sse_salen_sin_buffer_y_con_timeout_alto(conf: str, ruta: str) -> None:
    from app.seguridad.permisos import ACCESOS

    assert ("GET", ruta) in {(a.metodo, a.ruta) for a in ACCESOS}, "la ruta SSE existe en la API"
    sse = _resolver(_locations(conf), ruta)

    assert sse.modificador == "=" and sse.ruta == ruta
    assert "proxy_buffering off" in sse.cuerpo
    assert "proxy_cache off" in sse.cuerpo
    assert 'X-Accel-Buffering "no"' in sse.cuerpo
    assert "gzip off" in sse.cuerpo
    lectura = re.search(r"proxy_read_timeout\s+(\d+)([smhd]?)", sse.cuerpo)
    assert lectura is not None
    segundos = int(lectura.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[lectura.group(2)]
    assert segundos >= 3600, "el canal no puede cerrarse por inactividad en menos de una hora"


def prueba_el_proxy_conserva_el_mismo_origen_para_la_cookie(conf: str) -> None:
    proxy = (NGINX / "proxy-api.conf").read_text(encoding="utf-8")

    assert "proxy_pass http://api;" in proxy and "proxy_http_version 1.1" in proxy
    assert "proxy_set_header Host $http_host" in proxy
    assert "proxy_cookie" not in proxy and "Set-Cookie" not in conf, "la cookie pasa tal cual"
    assert re.search(r"upstream api \{\s*server 127\.0\.0\.1:8000;", conf)


def prueba_los_assets_con_hash_se_guardan_un_ano_y_el_resto_se_revalida(conf: str) -> None:
    locations = _locations(conf)
    assets = _resolver(locations, "/estaticos/spa/assets/index-abc123.js")
    otros = _resolver(locations, "/estaticos/centinela.css")

    assert "max-age=31536000" in assets.cuerpo and "immutable" in assets.cuerpo
    assert 'Cache-Control "no-cache"' in otros.cuerpo


def prueba_las_cabeceras_de_seguridad_estan_en_toda_respuesta_que_nginx_arma(conf: str) -> None:
    cabeceras = (NGINX / "cabeceras.conf").read_text(encoding="utf-8")

    for cabecera in (
        'X-Content-Type-Options "nosniff"',
        "Referrer-Policy",
        'X-Frame-Options "DENY"',
        "Content-Security-Policy",
    ):
        assert cabecera in cabeceras
    # `add_header` de una location reemplaza al del nivel de arriba: cada una las incluye.
    for loc in _locations(conf):
        if loc.ruta == "/nginx-salud" or loc.modificador == "~":
            continue
        assert "centinela/cabeceras.conf" in loc.cuerpo, f"{loc.ruta} no incluye las cabeceras"


def prueba_la_csp_no_permite_cdn_ni_evaluacion_dinamica(conf: str) -> None:
    csp = re.search(r'Content-Security-Policy "([^"]+)"', (NGINX / "cabeceras.conf").read_text())
    assert csp is not None
    politica = csp.group(1)

    assert "default-src 'self'" in politica
    assert "script-src 'self'" in politica and "'unsafe-eval'" not in politica
    assert "'unsafe-inline'" not in re.search(r"script-src [^;]+", politica).group(0)
    assert "frame-ancestors 'none'" in politica and "object-src 'none'" in politica
    assert not re.search(r"https?:|//", politica), "ningun origen externo: la SPA no usa CDN"


def prueba_la_spa_construida_no_pide_nada_a_otro_origen() -> None:
    """El index fuente solo referencia rutas del mismo origen (lo que la CSP exige)."""
    html = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")

    destinos = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert destinos and not [d for d in destinos if d.startswith(("http:", "https:", "//"))]
    assert "<script>" not in html and "onclick" not in html, "sin JavaScript en linea"


def prueba_nginx_escucha_en_un_puerto_sin_privilegio_y_sin_version(conf: str) -> None:
    assert "listen 8080" in conf
    assert "server_tokens off" in conf


# ---------------------------------------------------------------------------
# tareas.py
# ---------------------------------------------------------------------------


def prueba_arriba_con_app_levanta_web_y_api_y_abajo_los_baja(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("tareas_dos_servidores", RAIZ / "tareas.py")
    tareas = importlib.util.module_from_spec(spec)
    sys.modules["tareas_dos_servidores"] = tareas
    spec.loader.exec_module(tareas)
    try:
        monkeypatch.setattr(tareas, "_buscar", lambda programa: programa)
        monkeypatch.setattr(tareas, "ARCHIVO_ENV", RAIZ / "no-existe.env")
        monkeypatch.setenv("CENTINELA_MONGO__URI", "mongodb://localhost:27999/?replicaSet=rsfraude")

        assert tareas.main(["--dry-run", "arriba", "--con-app"]) == 0
        arriba = capsys.readouterr().out
        assert tareas.main(["--dry-run", "abajo"]) == 0
        abajo = capsys.readouterr().out
    finally:
        sys.modules.pop("tareas_dos_servidores", None)

    assert re.search(r"--profile app up -d --build --wait api web", arriba)
    assert re.search(r"--profile app down", abajo), "abajo incluye el perfil: baja api y web tambien"
