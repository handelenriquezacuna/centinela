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

## Verificación

```
docker compose -f infra/docker-compose.yml exec mongo1 mongosh --port 27018 --quiet --eval "rs.status().members.map(m => m.name + ' -> ' + m.stateStr)"
```

Debe listar un `PRIMARY` y dos `SECONDARY`.

## Apagar y reiniciar

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
