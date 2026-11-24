/*
 * EventSource de prueba: registra las instancias y deja emitir eventos a mano, con la
 * misma forma que el canal real (`event: alerta`, `data: {json}`).
 */
type Oyente = (evento: MessageEvent) => void;

export class EventSourceFalso {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instancias: EventSourceFalso[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readyState = EventSourceFalso.CONNECTING;
  onopen: ((evento: Event) => void) | null = null;
  onerror: ((evento: Event) => void) | null = null;
  private readonly oyentes = new Map<string, Oyente[]>();

  constructor(url: string, opciones?: EventSourceInit) {
    this.url = url;
    this.withCredentials = Boolean(opciones?.withCredentials);
    EventSourceFalso.instancias.push(this);
  }

  addEventListener(tipo: string, oyente: Oyente): void {
    this.oyentes.set(tipo, [...(this.oyentes.get(tipo) ?? []), oyente]);
  }

  close(): void {
    this.readyState = EventSourceFalso.CLOSED;
  }

  abrir(): void {
    this.readyState = EventSourceFalso.OPEN;
    this.onopen?.(new Event("open"));
  }

  fallar(cerrado = false): void {
    this.readyState = cerrado ? EventSourceFalso.CLOSED : EventSourceFalso.CONNECTING;
    this.onerror?.(new Event("error"));
  }

  emitir(tipo: string, cuerpo: unknown, id = ""): void {
    const evento = new MessageEvent(tipo, { data: JSON.stringify(cuerpo), lastEventId: id });
    for (const oyente of this.oyentes.get(tipo) ?? []) oyente(evento);
  }

  static ultima(): EventSourceFalso {
    const ultima = EventSourceFalso.instancias.at(-1);
    if (ultima === undefined) throw new Error("no se abrio ningun EventSource");
    return ultima;
  }
}
