# 05 · Infraestructura

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

`mongo2` y `mongo3` comparten el espacio de red de `mongo1`
(`network_mode: service:mongo1`). Así los tres se ven entre sí en `localhost` y
la máquina anfitriona los alcanza por los puertos publicados, **sin tocar
`/etc/hosts`** y sin permisos de administrador.

## Arranque

```bash
cd infra && docker compose up -d
docker exec -i fraude-mongo1 mongosh --port 27018 --quiet < ../scripts/init-replicaset.js
```

El script de inicialización es idempotente: si el replica set ya existe, no hace
nada. La elección del primario tarda entre 5 y 15 segundos.

## Conexión

Usar **siempre** la cadena del replica set completo. Conectarse a un nodo suelto
funciona para leer, pero rompe los Change Streams cuando cambia el primario:

```
mongodb://localhost:27018,localhost:27019,localhost:27020/antifraude?replicaSet=rsfraude
```

## Verificación

```bash
mongosh --port 27018 --eval "rs.status().members.map(m => m.name + ' → ' + m.stateStr)"
```

Debe listar un `PRIMARY` y dos `SECONDARY`.

## Ambiente aparte para el curso

El `mongod` de Homebrew en el puerto **27017** quedó configurado como replica set
`rs0` de un solo nodo. Es independiente del proyecto y sirve para prácticas y
simulaciones individuales, donde conviene tener Change Streams disponibles sin
levantar Docker.

| Ambiente | Puerto | Uso |
|---|---|---|
| `rs0` (Homebrew) | 27017 | Prácticas y simulaciones individuales |
| `rsfraude` (Docker) | 27018-27020 | El proyecto |

El respaldo de la configuración original está en
`/opt/homebrew/etc/mongod.conf.bak-20260909`.

## Nota para el día de la defensa

El portal se sirve **compilado** desde la API: un solo proceso corriendo. El rubro
que exige que el proyecto no dé error en tiempo de ejecución pesa demasiado como
para depender de un servidor de desarrollo con recarga en caliente.
