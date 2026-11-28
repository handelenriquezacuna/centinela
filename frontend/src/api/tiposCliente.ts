/*
 * Tipos del portal del cliente (T-82): GET y POST /api/v1/cliente/{token}, y la bandeja del
 * simulador (GET /api/v1/simulador-sms). Espejo de app/api/esquemas_cliente.py.
 */
import type { RespuestaCliente } from "./tipos";

export interface EstadoDelCaso {
  codigo: "recibida" | "en_revision" | "en_investigacion" | "fraude_confirmado" | "sin_fraude";
  titulo: string;
  detalle: string;
}

export interface EnlaceDelCliente {
  fase: "pendiente" | "respondida";
  puede_responder: boolean;
  transferencia: {
    monto_crc: number;
    fecha: string;
    cuenta_terminada_en: string;
    destino_enmascarado: string;
  };
  vence: string;
  respuesta: RespuestaCliente | null;
  caso: EstadoDelCaso | null;
  ayuda: { telefono: string; es_ejemplo: boolean };
  sondeo_segundos: number;
}

export interface MensajeSms {
  notificacion_id: string;
  alerta_id: string;
  para: string;
  fecha: string;
  estado_envio: "pendiente" | "enviado" | "fallido";
  texto: string | null;
  ruta_enlace: string | null;
  enlace: "vigente" | "usado" | "vencido" | null;
  respuesta: RespuestaCliente | null;
}

export interface BandejaSms {
  mensajes: MensajeSms[];
}
