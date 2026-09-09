#!/usr/bin/env python3
"""Envuelve el contenido de la pagina en un HTML autonomo para GitHub Pages.

El fuente solo trae el contenido. Servido como archivo estatico hacen falta
doctype, charset y viewport explicitos: sin charset los acentos se rompen al
abrir la pagina localmente, y sin viewport no es responsive.

Uso:  python3 scripts/build-pagina.py
"""
import pathlib, re, sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SRC = RAIZ / "docs" / "arquitectura" / "_contenido.html"
OUT = RAIZ / "docs" / "arquitectura" / "index.html"

contenido = SRC.read_text(encoding="utf-8")

# Todo lo anterior al primer elemento visible es <head>; el resto es <body>.
corte = re.search(r'\n(?=<div class="wrap">)', contenido)
if not corte:
    sys.exit("No encontre <div class=\"wrap\"> para separar head de body.")
cabeza, cuerpo = contenido[:corte.start()], contenido[corte.end():]

OUT.write_text(
    '<!doctype html>\n'
    '<html lang="es">\n'
    '<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="description" content="Arquitectura y evolucion de Centinela, '
    'sistema de monitoreo de fraude SINPE. Proyecto SC-609, Universidad Fidelitas.">\n'
    '<style>html{color-scheme:light dark}body{margin:0}img{max-width:100%}'
    '[hidden]{display:none!important}</style>\n'
    + cabeza + '\n'
    '</head>\n'
    '<body>\n'
    + cuerpo +
    '\n</body>\n</html>\n',
    encoding="utf-8")

print("Generado: %s (%d KB)" % (OUT.relative_to(RAIZ), OUT.stat().st_size // 1024))
