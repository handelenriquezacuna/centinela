# 05 · Infraestructura

Todo el equipo corre el mismo ambiente con Docker. Los comandos de esta página
son idénticos en **macOS, Linux y Windows**.

## Requisitos

| Herramienta | Para qué |
|---|---|
| Docker Desktop (o Docker Engine + Compose) | Levantar el replica set |
| `mongosh` *(opcional)* | Conectarse desde la máquina anfitriona. También se puede usar el que ya viene dentro del contenedor |
| MongoDB Compass *(opcional)* | Explorar los datos con interfaz gráfica |

No hace falta instalar MongoDB en la máquina: el servidor vive en los contenedores.

## Por qué replica set y no un `mongod` suelto

**No es una preferencia. Es un requisito duro del diseño.**

Los **Change Streams** y las **transacciones multi-documento** solo existen en
replica set. En un `mongod` standalone fallan así:

```
MongoServerError[Location40573]: The $changeStream stage is only supported on replica sets
```

Toda la detección en tiempo real de Centinela se apoya en Change Streams. Sin
replica set no hay motor, y sin motor no hay producto.

De paso, el clúster de tres nodos cubre la **Unidad 4 (modelos distribuidos)** y
sirve de insumo directo para la **Práctica 2**.

## Topología

| Nodo | Puerto | Rol |
|---|---|---|
| `fraude-mongo1` | 27018 | PRIMARY |
| `fraude-mongo2` | 27019 | SECONDARY |
| `fraude-mongo3` | 27020 | SECONDARY |

Se usan los puertos 27018-27020 a propósito: el **27017** se deja libre para que
quien tenga un MongoDB instalado en su máquina no choque con el del proyecto.

`mongo2` y `mongo3` comparten el espacio de red de `mongo1`
(`network_mode: service:mongo1`). Así los tres se ven entre sí en `localhost` y
la máquina anfitriona los alcanza por los puertos publicados, **sin editar el
archivo `hosts` del sistema** y sin permisos de administrador — que en Windows
es un trámite molesto.

## Arranque

Dos comandos, iguales en cualquier sistema operativo:

```
docker compose -f infra/docker-compose.yml up -d
```

```
docker compose -f infra/docker-compose.yml exec mongo1 mongosh --port 27018 --quiet --file /scripts/init-replicaset.js
```

La carpeta `scripts/` está montada dentro del contenedor como `/scripts`, por eso
el segundo comando usa `--file` en vez de redirección de shell: **el operador `<`
no existe en PowerShell**, y con `--file` el comando es el mismo para todos.

El script de inicialización es idempotente: si el replica set ya existe, no hace
nada. La elección del primario tarda entre 5 y 15 segundos.

## Conexión

Usar **siempre** la cadena del replica set completo. Conectarse a un nodo suelto
funciona para leer, pero rompe los Change Streams cuando cambia el primario:

```
mongodb://localhost:27018,localhost:27019,localhost:27020/antifraude?replicaSet=rsfraude
```

## Autenticacion y usuarios (H-25)

La base exige usuario y contrasena. Los tres nodos arrancan con un `keyFile` (lo agrega
`infra/mongo-entrada.sh`) y los puertos solo se publican en `127.0.0.1`.

| Usuario | Rol | Para que |
|---|---|---|
| `centinela_app` | `centinela_app_rol`: find, insert, update, remove, changeStream, indexStats, listCollections, listIndexes sobre `antifraude` | La aplicacion. Cubre transacciones y `$merge`. No crea indices ni borra colecciones o bases, ni toca usuarios |
| `centinela_analista` | `read` sobre `antifraude` | Compass y la defensa. No escribe |
| `centinela_admin` | `dbOwner` de `antifraude` (dbAdmin + userAdmin + readWrite) | Semillas, validadores, migraciones, usuarios |
| `centinela_pruebas` | `dbOwner` de las bases `antifraude_pruebas*` | Solo la suite de pruebas. No toca `antifraude` |

Todos viven en `antifraude`: se autentican con `authSource=antifraude`. Los crea
`scripts/semilla/04-usuarios.js`, idempotente.

Las contrasenas son aleatorias y estan en `.env` (en la raiz, ignorado por git). Lo crea
`python tareas.py arriba` la primera vez, igual que `infra/secretos/keyfile`. `.env.example`
es solo el molde. Nada de esto va al repo.

**Compass**, con el usuario analista (la contrasena es `MONGO_CLAVE_ANALISTA` de `.env`):

```
mongodb://centinela_analista:<MONGO_CLAVE_ANALISTA>@localhost:27018,localhost:27019,localhost:27020/antifraude?replicaSet=rsfraude&authSource=antifraude
```

**Base existente sin autenticacion** (con datos): `python tareas.py asegurar`. Cuenta los
documentos, levanta los nodos sin keyfile, crea los usuarios, recrea los nodos con
keyfile y vuelve a contar; falla si el conteo cambia. Los volumenes no se tocan. Si se
interrumpe, volver a correrlo. Una base nueva pasa por lo mismo dentro de `arriba`.

**Vuelta atras** (volver a una base sin autenticacion, con los datos):

1. `python tareas.py abajo` (sin `--borrar-datos`).
2. En `infra/docker-compose.yml` volver a `git show <commit-anterior>:infra/docker-compose.yml`
   (o `git checkout dev -- infra/docker-compose.yml` si `dev` aun no lo tiene).
3. `docker compose -f infra/docker-compose.yml up -d --wait`. Sin `--keyFile` el nodo
   arranca sin autenticacion y los usuarios quedan guardados pero inertes.
4. Quitar `MONGO_CLAVE_*` del entorno (o borrar `.env`): `tareas.py` vuelve a la URI sin
   credenciales.

Variables que usa `tareas.py`: `MONGO_CLAVE_APP`, `MONGO_CLAVE_ANALISTA`,
`MONGO_CLAVE_ADMIN`, `MONGO_CLAVE_PRUEBAS` y `CENTINELA_SESION__CLAVE`. La URI con usuario
llega al proceso por `CENTINELA_MONGO__URI`; `config/centinela.yml` no lleva secretos.

## Verificación

```
python tareas.py estado
```

Debe listar un `PRIMARY` y dos `SECONDARY`.

## Apagar y reiniciar

Los servicios llevan `restart: unless-stopped`: al reiniciar Docker Desktop los nodos vuelven solos y no quedan en `Exited (137)`. Un `down` explícito sí los deja apagados.

```
docker compose -f infra/docker-compose.yml down
```

Los datos sobreviven en volúmenes de Docker. Para empezar de cero —y volver a
inicializar el replica set— hay que borrarlos:

```
docker compose -f infra/docker-compose.yml down -v
```

## Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `Location40573` al usar Change Streams | Se está conectando a un nodo suelto | Usar la cadena completa con `replicaSet=rsfraude` |
| `port is already allocated` | Otro servicio ocupa 27018-27020 | Cambiar los puertos publicados en `docker-compose.yml` |
| `NotYetInitialized` | Falta el segundo comando del arranque | Correr el script de inicialización |
| El primario no aparece | La elección aún no termina | Esperar 15 segundos y verificar de nuevo |
| Los contenedores se caen al arrancar | Poca memoria asignada a Docker | Subir el límite en Docker Desktop → Settings → Resources |
