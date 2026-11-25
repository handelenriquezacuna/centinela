# 04 · Arquitectura

Los diagramas renderizan solos en GitHub. Este documento es **vivo**: la sección
de evolución se actualiza conforme cerramos etapas.

## 1. Reglas de oro

| Regla | Qué significa |
|---|---|
| **La ingesta no pasa por la API** | El generador escribe directo en MongoDB. La detección reacciona a la base de datos, no a nuestro endpoint |
| **El motor no sabe que existe una pantalla** | Se construye headless. Si el portal desaparece, el motor sigue creando alertas correctas |
| **Las reglas viven en datos, no en código** | Cambiar un umbral no requiere desplegar |
| **La lógica de lectura recurrente vive en el servidor** | Vistas y pipelines materializados; la API los consume, no los reimplementa |
| **Nada se recalcula dentro de una petición HTTP** | Los indicadores se materializan aparte; el tablero solo lee |
| **La alerta guarda por qué se creó** | Puntaje, reglas disparadas y versión de cada regla quedan dentro del documento |

## 2. Contexto

```mermaid
flowchart TB
    subgraph externo [Fuera del sistema]
        CLI([Cliente bancario<br/>víctima potencial])
        EST([Estafador<br/>vishing])
    end

    subgraph centinela [Centinela]
        SYS[Monitoreo de fraude<br/>en tiempo real]
    end

    subgraph usuarios [Usuarios]
        AG([Agente de fraude])
        SU([Supervisor])
        AD([Administrador])
    end

    EST -. "engaña por teléfono" .-> CLI
    CLI -- "autoriza transferencia<br/>a cuenta mula" --> SYS
    SYS -- "alerta priorizada<br/>con su motivo" --> AG
    SYS -- "indicadores del turno" --> SU
    AD -- "reglas y listas de riesgo" --> SYS
    AG -- "resuelve y deja rastro" --> SYS
```

## 3. Contenedores

```mermaid
flowchart LR
    subgraph gen [Generador]
        G[Python + Faker<br/>población, flujo normal,<br/>patrones de fraude]
    end

    subgraph api [API · FastAPI]
        R[Routers HTTP]
        S[Servicios]
        P[Repositorios<br/>único lugar con consultas]
        W[Canal SSE]
    end

    subgraph motor [Detección · módulo del mismo proceso]
        D1[D1 detección]
        D2[D2 notificación]
        D3[D3 indicadores]
    end

    subgraph mongo [MongoDB · replica set rsfraude]
        C[(12 colecciones<br/>13 al cerrar H-09)]
        V[Vistas guardadas]
        M[Pipelines $merge<br/>perfiles e indicadores]
        OL[(oplog)]
    end

    subgraph portal [Portal · SPA React + TypeScript]
        UI[Pantallas del agente<br/>build estatico de Vite]
    end

    G -- "escritura directa<br/>sin pasar por la API" --> C
    C -.-> OL
    OL == "Change Streams" ==> D1 & D2 & D3
    D1 --> C
    D2 --> SMTP[(SMTP pruebas)]
    D3 --> M --> C
    R -- "index.html y /estaticos/spa<br/>mismo proceso" --> UI
    UI -- "fetch JSON /api/v1<br/>cookie HttpOnly" --> R --> S --> P --> C
    P --> V
    OL == "Change Stream de alertas" ==> W
    W == "SSE · eventos JSON" ==> UI
```

El portal es una SPA React + TypeScript construida con Vite y servida por el **mismo**
FastAPI: `uvicorn app.main:app` sigue siendo el único proceso y el único comando. No hay
servidor Node en producción (Node solo construye), ni Next.js, ni WebSocket, ni bus en
memoria. Detalle en la sección 6b.

## 4. El flujo que define el producto

```mermaid
sequenceDiagram
    participant G as Generador
    participant DB as MongoDB
    participant D1 as Motor D1
    participant WS as Canal SSE
    participant AG as Agente

    G->>DB: insert transacción ₡280.000 → destino nuevo
    DB-->>D1: Change Stream: evento insert
    D1->>DB: lee perfil del cliente
    D1->>DB: lee reglas activas
    Note over D1: evalúa · R-01 (35) + R-03 (25) + R-05 (15)<br/>puntaje 75 → CRÍTICA
    D1->>DB: insert alerta con reglas disparadas
    DB-->>WS: Change Stream de alertas
    Note over WS: el canal lee lo persistido,<br/>no un bus en memoria
    WS-->>AG: aparece en el panel (< 2 s)
    AG->>DB: resuelve → confirmada
    Note over DB: la acción se agrega al<br/>historial embebido de la alerta
```

## 5. Decisiones de modelado

```mermaid
erDiagram
    CLIENTES ||--o{ CUENTAS : "posee"
    CLIENTES ||--o| PERFILES_COMPORTAMIENTO : "materializa (_id = cliente_id)"
    CUENTAS ||--o{ TRANSACCIONES : "origina (cuenta_origen)"
    TRANSACCIONES ||--o| ALERTAS : "puede generar (transaccion_id)"
    AGENTES |o--o{ ALERTAS : "asignada a (agente_id, opcional)"
    CASOS |o--o{ ALERTAS : "agrupa (casos.alertas)"
    REGLAS_DETECCION }o--o{ ALERTAS : "justifica (copia embebida)"
    POLITICA_DETECCION |o--o{ ALERTAS : "rige (politica.politica_id)"
    ALERTAS ||--o{ NOTIFICACIONES : "dispara (alerta_id)"
    LISTAS_RIESGO |o--o{ TRANSACCIONES : "marca cuenta destino (numero SINPE)"
    TRANSACCIONES |o..o{ DETECCION_ESTADO : "descarte (transaccion_id) o rasgo_grafo (cuenta)"
    INDICADORES_DIARIOS ||..o{ ALERTAS : "resume por dia"

    CLIENTES {
        string _id PK "CLI-001"
        string nombre
        string cedula "unica"
        date fecha_registro
        int esquema_version
    }
    CUENTAS {
        string _id PK "CTA-001"
        string cuenta_sinpe "unica"
        string cliente_id FK "CLI-001"
        string entidad "opcional"
        int esquema_version
    }
    PERFILES_COMPORTAMIENTO {
        string _id PK "igual a cliente_id"
        long promedio_crc
        long maximo_crc
        int num_transferencias
        object horario_habitual "embebido: inicio y fin HH:MM"
        array destinos_frecuentes "embebido: numeros SINPE"
        int esquema_version
    }
    REGLAS_DETECCION {
        string _id PK "REG-001"
        string codigo "R-01, unica"
        string descripcion
        string tipo
        long umbral "int, long o double"
        object parametros "opcional"
        int peso
        bool activa
        int version
        array versiones_anteriores "embebido con historia"
        int esquema_version
    }
    LISTAS_RIESGO {
        string _id PK "RSK-001"
        string cuenta "numero SINPE, unica"
        string motivo
        date fecha_reporte
        bool vigente
        int esquema_version
    }
    TRANSACCIONES {
        string _id PK "TXN-001"
        string cuenta_origen FK "CTA-001"
        string cuenta_destino "numero SINPE"
        string sinpe_origen "numero SINPE"
        long monto_crc "colones enteros, 64 bits"
        string moneda "CRC"
        string canal "sinpe_movil"
        date fecha "UTC"
        object simulacion "solo generador, etiqueta oculta"
        int esquema_version
    }
    AGENTES {
        string _id PK "AGT-001"
        string nombre
        string rol "agente, supervisor o administrador"
        bool activo
        object credencial "embebida, opcional"
        int esquema_version
    }
    ALERTAS {
        string _id PK "ALR-001"
        string transaccion_id FK "unica: idempotencia"
        string cuenta_origen "numero SINPE, copia"
        string cuenta_destino "numero SINPE, copia"
        long monto_crc "copia"
        string moneda "copia"
        string cliente_nombre "copia"
        array reglas_disparadas "embebido con version"
        object politica "embebido, solo v2"
        int puntaje
        string severidad "MEDIA, ALTA o CRITICA"
        string estado "maquina de estados"
        string agente_id FK "opcional"
        date fecha_creacion
        array historial "embebido"
        int esquema_version
    }
    CASOS {
        string _id PK "CAS-001"
        array alertas FK "ALR-001, referencias"
        string estado "investigacion o cerrado"
        date fecha_apertura "opcional"
        string cuenta_destino "numero SINPE, opcional"
        string agente_id FK "opcional"
        int esquema_version
    }
    NOTIFICACIONES {
        string _id PK "NOT-001"
        string alerta_id FK "ALR-001"
        string canal "correo o sms"
        string estado_envio "pendiente, enviado o fallido"
        date fecha
        string detalle_error "opcional"
        int esquema_version
    }
    INDICADORES_DIARIOS {
        string _id PK "fecha YYYY-MM-DD"
        string fecha "texto YYYY-MM-DD"
        int total_alertas
        object por_severidad "embebido"
        object por_estado "embebido"
        object por_agente "embebido"
        object por_hora "embebido"
        long decision_segundos_total
        long monto_protegido_crc
        long pendientes
        date pendiente_mas_antigua
        date calculado_en
        int esquema_version
    }
    POLITICA_DETECCION {
        string _id PK "POL-001"
        string descripcion
        bool vigente "unica con true"
        int version
        int umbral_alerta
        int puntaje_maximo
        array cortes_severidad "embebido"
        object notificacion "embebido"
        date vigente_desde
        array versiones_anteriores "embebido, opcional"
        int esquema_version
    }
    DETECCION_ESTADO {
        string _id PK "disparador, disparador:TXN o disparador:cuenta"
        string clase "reanudacion, descarte, rasgo_grafo o rasgo_grafo_corrida"
        string disparador
        object token_reanudacion "solo reanudacion"
        string transaccion_id FK "solo descarte"
        string cuenta "solo rasgo_grafo"
        int fan_in "solo rasgo_grafo"
        long monto_recibido_crc "solo rasgo_grafo"
        date actualizado_en "reanudacion, descarte y corrida"
        int esquema_version
    }
```

`deteccion_estado` no es una coleccion de dominio: guarda cuatro clases de documento,
distinguidas por `clase`. `reanudacion` tiene `_id` = nombre del disparador
(`d1_deteccion`) y guarda el token del change stream. `descarte` tiene `_id` =
`disparador:TXN-NNN` y es un evento que agoto los reintentos. `rasgo_grafo` tiene
`_id` = `d3_rasgos_grafo:<cuenta SINPE>` y guarda los rasgos de grafo de una cuenta
destino. `rasgo_grafo_corrida` tiene `_id` = `d3_rasgos_grafo` y dice de que rango son
esos rasgos.

| Decisión | Elección | Por qué |
|---|---|---|
| Historial de acciones de una alerta | **Embebido** | Se lee siempre con la alerta, nunca solo. Crece acotado |
| Alerta → agente | **Referencia** | Cambia de dueño con frecuencia; duplicar el agente sería incoherente |
| Reglas disparadas dentro de la alerta | **Copia embebida con versión** | La alerta debe poder explicarse dentro de un año, aunque la regla haya cambiado |
| Perfil de comportamiento | **Colección materializada** | Se calcula caro y se lee en cada transacción |
| Indicadores | **Colección materializada** | El tablero abre al instante; nada se agrega dentro de la petición |

### Las 13 colecciones

Once de dominio (Práctica 1) y dos de operación. Índices tomados de
`scripts/semilla/02-indices.js`; `_id` es el índice por defecto cuando no hay otro.

| Colección | Para qué sirve | Embebe o referencia | Índice principal |
|---|---|---|---|
| `clientes` | Titulares de cuentas; búsqueda por cédula en atención al cliente | Referenciada por `cuentas.cliente_id` | `idx_clientes_cedula` (único) |
| `cuentas` | Cuentas SINPE Móvil de cada cliente | Referencia a `clientes`; `cuenta_sinpe` es el número SINPE | `idx_cuentas_sinpe` (único); también `idx_cuentas_cliente` |
| `perfiles_comportamiento` | Hábitos de cada cliente, leídos en cada transacción | Embebe `horario_habitual` y `destinos_frecuentes`; `_id` = `cliente_id` | `_id` (sin índice adicional) |
| `reglas_deteccion` | Catálogo de reglas R-xx con umbral, peso y versión | Embebe `versiones_anteriores` (historia de la regla) | `idx_reglas_codigo` (único) |
| `listas_riesgo` | Cuentas marcadas como mula (regla R-07) | Referencia por número SINPE a `transacciones.cuenta_destino` | `idx_listas_riesgo_cuenta` (único) |
| `transacciones` | Transferencias SINPE Móvil: el hecho original | Referencia a `cuentas` (`cuenta_origen`); embebe `simulacion` (solo generador) | `idx_transacciones_cuenta_origen_fecha` |
| `agentes` | Quién atiende las alertas y con qué rol | Embebe `credencial` (hash y sal) | `idx_agentes_rol` |
| `alertas` | Una alerta por transacción: puntaje, severidad, estado e historial | Embebe `historial`, `reglas_disparadas` (copia con versión) y `politica`; referencia `transaccion_id` y `agente_id` | `idx_alertas_estado_severidad_fecha` (panel); `idx_alertas_transaccion_id` (único, idempotencia) |
| `casos` | Agrupa alertas escaladas, por cuenta destino | Referencia alertas por `_id` (arreglo `alertas`) y al agente | `idx_casos_estado` |
| `notificaciones` | Correo o SMS enviado por una alerta | Referencia a `alertas` por `alerta_id` | `idx_notificaciones_alerta` |
| `indicadores_diarios` | Resumen del día para el tablero | Embebe los contadores (`por_severidad`, `por_estado`, `por_agente`, `por_hora`) | `_id` = fecha (sin índice adicional) |
| `politica_deteccion` (operación) | Cortes de severidad y umbral de alerta, con historia | Embebe `cortes_severidad`, `notificacion` y `versiones_anteriores` | `idx_politica_vigente` (único parcial sobre `vigente: true`) |
| `deteccion_estado` (operación) | Token de reanudación del motor, descartes y rasgos de grafo | Referencia `transaccion_id` (descarte) y cuenta SINPE (rasgo) | `_id` natural (sin índice adicional) |

## 6. Evolución: cómo llegamos ahí

Seis etapas. **Cada una deja algo que funciona**, no un pedazo inerte.

### E0 · Fundación — terminada

```mermaid
flowchart LR
    M[(MongoDB<br/>replica set 3 nodos)]
    style M fill:#1a4d2e,color:#fff
```

Replica set `rsfraude` corriendo con Change Streams, transacciones
multi-documento y `$graphLookup` verificados. Repo estructurado.
**Se puede demostrar:** `rs.status()` con un primario y dos secundarios.

---

### E1 · Los datos existen

```mermaid
flowchart LR
    G[Generador] ==> M[(MongoDB<br/>11 colecciones<br/>validadores + índices)]
    style M fill:#1a4d2e,color:#fff
    style G fill:#1a4d2e,color:#fff
```

Historias **H-01 a H-06, H-12, H-26 (adelantada)**. Hay clientes con hábitos,
transacciones que respetan esos hábitos, fraude inyectado como minoría, consultas
que usan índice, y el documento ya lleva versión de esquema con su migración.
**Se puede demostrar:** 100.000 transacciones consultadas con `IXSCAN`.
**El recorte H-01+H-02 es la Práctica 1 (28 sep); H-26 es la Práctica 4 (19 oct).**

---

### E2 · El motor ve — *aquí el proyecto deja de ser un CRUD*

```mermaid
flowchart LR
    G[Generador] ==> M[(MongoDB)]
    M -. oplog .-> D1[Motor D1]
    D1 ==> A[(alertas)]
    P[Pipeline perfiles] --> M
    style D1 fill:#1a4d2e,color:#fff
    style A fill:#1a4d2e,color:#fff
    style P fill:#1a4d2e,color:#fff
```

Historias **H-07 a H-10, H-21**. El Change Stream reacciona, evalúa contra reglas
configurables y crea alertas explicadas. Todavía **sin una sola pantalla**.
**Se puede demostrar:** correr el generador y ver alertas apareciendo en `mongosh`,
con el reporte de efectividad diciendo cuánto fraude se atrapó.

---

### E3 · El agente ve — meta del Avance 2 (16 nov)

```mermaid
flowchart LR
    M[(MongoDB)] -.-> D1[Detección D1]
    D1 --> M
    M -. "Change Stream<br/>de alertas" .-> API[API FastAPI]
    API == SSE ==> UI[Portal: Jinja y HTMX,<br/>SPA React desde T-67]
    API --> M
    style API fill:#1a4d2e,color:#fff
    style UI fill:#1a4d2e,color:#fff
```

Historias **H-13 a H-15, H-17 a H-19**. Las alertas aparecen solas en el panel,
el agente las resuelve y queda rastro. **Es exactamente lo que pide el Avance 2:
pantallas ejecutando con toma de decisiones.**

---

### E4 · Inteligencia

```mermaid
flowchart LR
    M[(MongoDB)] --> GL[$graphLookup<br/>red de mulas]
    M --> MV[Vistas + $merge<br/>indicadores]
    D2[D2 correo] --> SMTP[(SMTP)]
    GL --> UI[Portal]
    MV --> UI
    style GL fill:#1a4d2e,color:#fff
    style MV fill:#1a4d2e,color:#fff
    style D2 fill:#1a4d2e,color:#fff
```

Historias **H-11, H-16, H-20, H-22 a H-24**. La cadena de mulas se recorre y se
dibuja, los indicadores se materializan, el correo sale.
**Es donde el sistema deja de reportar hechos sueltos y empieza a explicar un patrón.**

---

### E5 · Endurecimiento

```mermaid
flowchart LR
    SEC[Autenticación<br/>y roles] --> API[API]
    ARC[Archivado] --> M[(MongoDB)]
    style SEC fill:#1a4d2e,color:#fff
    style ARC fill:#1a4d2e,color:#fff
```

Historias **H-25, H-27** (H-26 ya se hizo en E1, ver arriba). Roles y permisos,
purga del histórico. Aquí el sistema se vuelve defendible, no solo demostrable.

---

## 6b. El portal: SPA React servida por el monolito (T-61)

Decisiones D-60 a D-63, D-69 y D-71. Las siete pantallas se migraron en las fases 1, 2A,
2B y 2C, y desde T-67 la SPA es **el portal por omisión** (`portal.interfaz: spa`), con
todos los recorridos de la defensa en e2e contra ella. Jinja + HTMX queda vivo como
**respaldo hasta el 2 de diciembre**: se vuelve a él en un paso, sin tocar archivos, con
`CENTINELA_PORTAL__INTERFAZ=htmx`. T-68 lo retira.

```mermaid
flowchart LR
    subgraph navegador [Navegador]
        RR[React Router<br/>una ruta por pantalla]
        TQ[TanStack Query<br/>datos y cache]
        ES[EventSource<br/>canal JSON]
        FMT[formatos.ts<br/>colones y hora CR]
    end

    subgraph proceso [uvicorn app.main:app · un proceso]
        SPA[rutas de pagina<br/>devuelven index.html]
        EST["/estaticos/spa<br/>build de Vite"]
        API["/api/v1 JSON<br/>forma única del error"]
        GU[guardia de permisos<br/>tabla única]
        SSE["/api/v1/alertas/eventos"]
        PURO[funciones puras<br/>detalle, casos, sankey, gráficos]
        REP[app/repos]
    end

    RR --> SPA
    RR --> EST
    TQ --> API
    ES --> SSE
    GU -. "antes de toda ruta" .- SPA & API & SSE
    API --> PURO --> REP --> M[(MongoDB)]
    SSE -- "change stream de alertas" --> M
```

| Pieza | Decisión |
|---|---|
| Servir | Las rutas de página (`/`, `/panel`, `/detalle/...`, `/red-mulas/...`, `/busqueda`, `/tablero`, `/reglas`, `/login`) devuelven el `index.html` del build; JS y CSS salen del montaje `/estaticos` de siempre. `/api/v1`, `/estado` y `/salud` no cambian |
| Calcular | El servidor calcula todo lo que se dibuja: la explicación del puntaje, los casos por cuenta destino, la disposición del Sankey y del grafo (nodos, bandas con ancho entero, columnas). La SPA solo dibuja SVG; no hay librería de grafos en el navegador |
| Datos | TanStack Query sobre la API JSON. Cada lectura comparte su función pura con la página HTMX equivalente, así que las dos interfaces no divergen mientras conviven |
| Tiempo real | `EventSource` sobre `GET /api/v1/alertas/eventos`: el mismo canal que el de HTMX (mismo change stream, mismo `id`, misma reanudación, mismo `resincronizar`) con cuerpo JSON |
| Sesión | `POST /api/v1/sesion` deja la cookie HttpOnly y SameSite=Strict; la SPA nunca guarda el token. 401 y 403 en la forma única del error; sin sesión se vuelve al login con el destino, con la misma regla anti redirección abierta |
| Permisos | La tabla única de `app/seguridad/permisos.py`. La SPA recibe en `GET /api/v1/sesion` qué páginas puede abrir el rol y no copia la tabla |
| Convivencia | `portal.interfaz` elige qué interfaz sirven las rutas de página: `spa` por omisión, `htmx` como respaldo hasta T-68. Mientras viva el respaldo, los fragmentos HTMX siguen registrados con las dos |
| Imagen | `infra/Dockerfile` en dos etapas: Node 26.8.1 (la de `frontend/.nvmrc`) hace `npm ci` y el build; la imagen que corre solo copia `app/estaticos/spa/` y no lleva Node. Corre con un usuario sin privilegios y con `HEALTHCHECK` sobre `/salud` |

## 7. Calendario de etapas

Revisado el 2026-09-20 contra las fechas reales del curso (con la corrección de
Práctica 1 confirmada en clase). Dos ajustes respecto a la versión anterior:
**H-26 se adelantó** de E5 a E1 (la Práctica 4 del curso, "sellos de versiones",
vence el 19 de octubre, mucho antes de diciembre), y se agregó la sección de
**hitos del curso** superpuesta a las etapas propias, para ver de un vistazo
qué entrega académica cae dentro de qué etapa técnica.

```mermaid
gantt
    dateFormat YYYY-MM-DD
    axisFormat %d %b
    title Evolución de la arquitectura vs. calendario del curso

    section Fundación
    E0 Infraestructura           :done, e0, 2026-09-09, 2026-09-14
    section Datos
    E1 Los datos existen         :e1, 2026-09-15, 2026-10-05
    H-26 versionado (adelantada) :h26, 2026-09-15, 2026-10-19
    section Motor
    E2 El motor ve               :e2, 2026-10-06, 2026-10-26
    section Visible
    E3 El agente ve              :e3, 2026-10-27, 2026-11-16
    section Inteligencia
    E4 Grafos e indicadores      :e4, 2026-11-17, 2026-11-30
    section Cierre
    E5 Endurecimiento            :e5, 2026-12-01, 2026-12-07

    section Entregas del curso
    Práctica 1 + 2                :milestone, 2026-09-28, 0d
    Simulación I (individual)     :milestone, 2026-10-05, 0d
    Práctica 3                    :milestone, 2026-10-12, 0d
    Práctica 4 (sellos versiones) :milestone, 2026-10-19, 0d
    Avance 1 IEEE                 :milestone, 2026-10-26, 0d
    Simulación II (individual)    :milestone, 2026-11-09, 0d
    Avance 2 pantallas            :milestone, 2026-11-16, 0d
    Simulación III (individual)   :milestone, 2026-11-30, 0d
    Entrega final y defensa       :milestone, 2026-12-07, 0d
```

**Lectura de esta tabla, no del programa impreso — reparto detallado con
paquetes por semana en `sc609-reparto-semanal` del cerebro.**

## 8. Glosario

| Término | Qué es |
|---|---|
| **Change Stream** | Suscripción al flujo de cambios de MongoDB. Es cómo el motor se entera de una transacción nueva sin preguntar cada segundo |
| **oplog** | Bitácora de operaciones del replica set. Es lo que hace posibles los Change Streams |
| **SSE** | *Server-Sent Events.* Flujo HTTP de una sola dirección, del servidor al navegador. Es cómo la alerta nueva llega al panel. Se eligió sobre WebSocket porque el panel solo necesita recibir, y el navegador reconecta solo |
| **HTMX** | Biblioteca que deja al servidor devolver HTML e insertarlo en la página, en vez de devolver JSON y armar la pantalla en el navegador. Fue el portal hasta T-67; queda como respaldo (`CENTINELA_PORTAL__INTERFAZ=htmx`) hasta T-68 |
| **SPA** | *Single Page Application.* La página se carga una vez y React cambia de pantalla sin pedir HTML nuevo; los datos llegan como JSON. La de Centinela se construye con Vite y la sirve el mismo FastAPI |
| **Token de reanudación** | Marca de posición en un Change Stream. El canal SSE la manda como identificador del evento; el navegador la devuelve al reconectar y el servidor retoma desde ahí. Si el oplog ya rodó, el token deja de servir y el panel vuelve a pedir su cola completa |
| **Replica set** | Grupo de nodos con los mismos datos. Obligatorio para Change Streams y transacciones |
| **Pipeline de agregación** | Secuencia de etapas que transforma documentos. El equivalente de un `GROUP BY` con esteroides |
| **`$merge`** | Etapa final que escribe el resultado del pipeline en una colección. Así materializamos perfiles e indicadores |
| **Vista** | Pipeline guardado en el servidor que se consulta como si fuera una colección |
| **`$graphLookup`** | Etapa que recorre relaciones recursivamente. Con esto seguimos la cadena de cuentas mula |
| **Documento embebido** | Dato que vive dentro de otro. Se usa cuando siempre se leen juntos |
| **Referencia** | Puntero al id de otro documento. Se usa cuando la relación cambia o el dato se comparte |
| **Vishing** | Estafa telefónica donde el atacante suplanta al banco para que la víctima transfiera |
| **Cuenta mula** | Cuenta usada para recibir dinero robado y moverlo rápido |
| **Perfil de comportamiento** | Resumen estadístico de cómo opera normalmente un cliente |
| **Falso positivo** | Alerta que resultó ser una operación legítima |
