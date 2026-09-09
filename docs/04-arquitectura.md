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
        W[WebSocket]
    end

    subgraph motor [Motor · proceso aparte]
        D1[D1 detección]
        D2[D2 notificación]
        D3[D3 indicadores]
    end

    subgraph mongo [MongoDB · replica set rsfraude]
        C[(11 colecciones)]
        V[Vistas guardadas]
        M[Pipelines $merge<br/>perfiles e indicadores]
        OL[(oplog)]
    end

    subgraph portal [Portal · React]
        UI[Pantallas del agente]
    end

    G -- "escritura directa<br/>sin pasar por la API" --> C
    C -.-> OL
    OL == "Change Streams" ==> D1 & D2 & D3
    D1 --> C
    D2 --> SMTP[(SMTP pruebas)]
    D3 --> M --> C
    UI --> R --> S --> P --> C
    P --> V
    D1 -. "evento" .-> W
    W == "tiempo real" ==> UI
```

## 4. El flujo que define el producto

```mermaid
sequenceDiagram
    participant G as Generador
    participant DB as MongoDB
    participant D1 as Motor D1
    participant WS as WebSocket
    participant AG as Agente

    G->>DB: insert transacción ₡750.000 → destino nuevo
    DB-->>D1: Change Stream: evento insert
    D1->>DB: lee perfil del cliente
    D1->>DB: lee reglas activas
    Note over D1: evalúa · R-01 (35) + R-03 (25) + R-05 (15)<br/>puntaje 75 → CRÍTICA
    D1->>DB: insert alerta con reglas disparadas
    D1->>WS: emite alerta
    WS-->>AG: aparece en el panel (< 2 s)
    AG->>DB: resuelve → confirmada
    Note over DB: la acción se agrega al<br/>historial embebido de la alerta
```

## 5. Decisiones de modelado

```mermaid
erDiagram
    CLIENTES ||--o{ CUENTAS : "posee"
    CUENTAS ||--o{ TRANSACCIONES : "origina"
    TRANSACCIONES ||--o| ALERTAS : "puede generar"
    ALERTAS }o--|| AGENTES : "asignada a"
    ALERTAS }o--o| CASOS : "escala a"
    REGLAS_DETECCION ||--o{ ALERTAS : "justifica"
    CLIENTES ||--|| PERFILES_COMPORTAMIENTO : "materializa"
    LISTAS_RIESGO ||--o{ ALERTAS : "alimenta"
    ALERTAS ||--o{ NOTIFICACIONES : "dispara"
```

| Decisión | Elección | Por qué |
|---|---|---|
| Historial de acciones de una alerta | **Embebido** | Se lee siempre con la alerta, nunca solo. Crece acotado |
| Alerta → agente | **Referencia** | Cambia de dueño con frecuencia; duplicar el agente sería incoherente |
| Reglas disparadas dentro de la alerta | **Copia embebida con versión** | La alerta debe poder explicarse dentro de un año, aunque la regla haya cambiado |
| Perfil de comportamiento | **Colección materializada** | Se calcula caro y se lee en cada transacción |
| Indicadores | **Colección materializada** | El tablero abre al instante; nada se agrega dentro de la petición |

## 6. Evolución: cómo llegamos ahí

Seis etapas. **Cada una deja algo que funciona**, no un pedazo inerte.

### E0 · Fundación — ✅ terminada

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

Historias **H-01 a H-06, H-12**. Hay clientes con hábitos, transacciones que
respetan esos hábitos, fraude inyectado como minoría, y consultas que usan índice.
**Se puede demostrar:** 100.000 transacciones consultadas con `IXSCAN`.
**Alimenta la Práctica 1 del 21 de septiembre.**

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
    M[(MongoDB)] -.-> D1[Motor D1]
    D1 --> API[API FastAPI]
    API == WebSocket ==> UI[Portal React]
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
    MIG[Migración v1→v2] --> M[(MongoDB)]
    ARC[Archivado] --> M
    style SEC fill:#1a4d2e,color:#fff
    style MIG fill:#1a4d2e,color:#fff
    style ARC fill:#1a4d2e,color:#fff
```

Historias **H-25 a H-27**. Roles, versionado de esquema con migración real,
purga del histórico. Aquí el sistema se vuelve defendible, no solo demostrable.

---

## 7. Calendario de etapas

```mermaid
gantt
    dateFormat YYYY-MM-DD
    axisFormat %d %b
    title Evolución de la arquitectura

    section Fundación
    E0 Infraestructura           :done, e0, 2026-09-09, 2026-09-14
    section Datos
    E1 Los datos existen         :e1, 2026-09-15, 2026-10-05
    section Motor
    E2 El motor ve               :e2, 2026-10-06, 2026-10-26
    section Visible
    E3 El agente ve              :e3, 2026-10-27, 2026-11-16
    section Inteligencia
    E4 Grafos e indicadores      :e4, 2026-11-17, 2026-11-30
    section Cierre
    E5 Endurecimiento            :e5, 2026-12-01, 2026-12-07

    section Hitos
    Avance 1 IEEE                :milestone, 2026-10-26, 0d
    Avance 2 pantallas           :milestone, 2026-11-16, 0d
    Entrega final y defensa      :milestone, 2026-12-07, 0d
```

## 8. Glosario

| Término | Qué es |
|---|---|
| **Change Stream** | Suscripción al flujo de cambios de MongoDB. Es cómo el motor se entera de una transacción nueva sin preguntar cada segundo |
| **oplog** | Bitácora de operaciones del replica set. Es lo que hace posibles los Change Streams |
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
