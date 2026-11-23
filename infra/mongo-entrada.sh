#!/bin/bash
# Punto de entrada de los nodos de Mongo (H-25).
#
# Agrega --keyFile al comando, que es lo que activa la autenticacion de cliente y la
# autenticacion entre miembros del replica set. El keyfile lo genera `python tareas.py`
# en infra/secretos/ (ignorado por git, permisos 400). Se copia a /data/keyfile con el
# dueno y los permisos que mongod exige: un archivo montado conserva el dueno del
# anfitrion y mongod lo rechazaria.
#
# CENTINELA_SIN_AUTH=1 arranca SIN keyfile. Solo lo usa el paso de `tareas.py asegurar`
# que crea los usuarios (necesita la auth apagada para crear el primero); no es una
# configuracion de trabajo. Se invoca con `bash`, no como ejecutable: asi no depende del
# bit de ejecucion del archivo, que Windows no conserva.
set -euo pipefail

if [ -z "${CENTINELA_SIN_AUTH:-}" ]; then
  if [ ! -s /secretos/keyfile ]; then
    echo "falta infra/secretos/keyfile. Correr: python tareas.py arriba" >&2
    exit 1
  fi
  install -m 400 -o mongodb -g mongodb /secretos/keyfile /data/keyfile
  set -- "$@" --keyFile /data/keyfile
fi

exec docker-entrypoint.sh "$@"
