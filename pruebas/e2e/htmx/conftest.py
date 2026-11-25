"""Las e2e del respaldo HTMX (T-67): los mismos recorridos de siempre, contra `servidor_htmx`.

La SPA es el portal por omision y `servidor_e2e` la sirve; estas pruebas siguen verificando
las paginas Jinja + HTMX mientras existan (T-68 las retira junto con este directorio). Cada
contexto del navegador de este directorio nace con la URL del servidor HTMX, asi que
`page.goto("/panel")` e `iniciar_sesion` van a el sin cambiar una linea de las pruebas.

Por que se redefine `browser_context_args` y NO `base_url`: las dos son de sesion en
pytest-playwright, y la del plugin se calcula una vez con el `base_url` de quien la pida
primero. Si este directorio cambiara `base_url`, la sesion entera (tambien las pruebas de la
SPA) podia quedar apuntando al servidor HTMX segun el orden de ejecucion. `base_url` sigue
siendo el de la SPA; la prueba que necesita la URL HTMX pide `servidor_htmx`.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any], servidor_htmx: str
) -> dict[str, Any]:
    return {**browser_context_args, "base_url": servidor_htmx}
