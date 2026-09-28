"""El arbol de documentacion no tiene enlaces roto.

Existe porque ya paso: se borraron `docs/06-avance1-checklist.md` y
`docs/07-practica1-codigo.md` y el README siguio enlazandolos. Un enlace roto en el
README es lo primero que ve quien abre el repo, incluido el profesor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

# [texto](destino), ignorando imagenes y anclas puras.
PATRON_ENLACE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")

DOCUMENTOS = [RAIZ / "README.md", *sorted((RAIZ / "docs").glob("*.md"))]


def enlaces_relativos(documento: Path) -> list[str]:
    destinos = PATRON_ENLACE.findall(documento.read_text(encoding="utf-8"))
    return [
        destino
        for destino in destinos
        if not destino.startswith(("http://", "https://", "mailto:", "#"))
    ]


@pytest.mark.parametrize(
    "documento", DOCUMENTOS, ids=lambda ruta: str(ruta.relative_to(RAIZ))
)
def prueba_los_enlaces_apuntan_a_algo_que_existe(documento: Path) -> None:
    roto: list[str] = []

    for destino in enlaces_relativos(documento):
        sin_ancla = destino.split("#", 1)[0]
        if not sin_ancla:
            continue
        if not (documento.parent / sin_ancla).resolve().exists():
            roto.append(destino)

    assert not roto, f"enlaces roto en {documento.relative_to(RAIZ)}: {roto}"


def prueba_el_readme_no_enlaza_los_documentos_borrados() -> None:
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")

    for borrado in ("06-avance1-checklist.md", "07-practica1-codigo.md"):
        assert borrado not in texto, f"el README todavia enlaza {borrado}, que se borro"


def prueba_el_readme_enlaza_el_documento_de_plataforma() -> None:
    """Es el unico lugar versionado con los contratos: tiene que encontrarse."""
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")

    assert "docs/06-plataforma.md" in texto
    assert (RAIZ / "docs" / "06-plataforma.md").is_file()


def prueba_el_documento_de_plataforma_cubre_los_seis_contratos() -> None:
    """Los seis que H-00A tiene que dejar escritos, ni uno menos."""
    texto = (RAIZ / "docs" / "06-plataforma.md").read_text(encoding="utf-8")

    for contrato in (
        "monto_crc",
        "esquema_version",
        "ISODate",
        "YYYY-MM-DD",
        "snake_case",
        "en_revision",
        "NumberLong",
        "Int64",
        "politica_deteccion",
    ):
        assert contrato in texto, f"docs/06-plataforma.md no documenta {contrato}"


def prueba_no_hay_emojis_en_la_documentacion_nueva() -> None:
    """Regla del repo. Se revisa lo que H-00A escribio, no los documentos heredados."""
    rangos = ((0x1F300, 0x1FAFF), (0x1F000, 0x1F2FF), (0x2600, 0x27BF), (0xFE0F, 0xFE0F))

    infractores: list[str] = []
    for documento in (RAIZ / "docs" / "06-plataforma.md", RAIZ / "README.md"):
        for numero, linea in enumerate(
            documento.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for caracter in linea:
                if any(a <= ord(caracter) <= b for a, b in rangos):
                    infractores.append(
                        f"{documento.name}:{numero} tiene {caracter!r}"
                    )

    assert not infractores, "sin emojis: " + "\n".join(infractores)
