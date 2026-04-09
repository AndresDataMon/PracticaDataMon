"""
Monitoriza una cuenta de Gmail y extrae adjuntos PDF de los correos
entrantes no leídos para su procesamiento como facturas.

Autenticación:
  - OAuth 2.0 con google-auth-oauthlib
  - Primera ejecución: abre el navegador para autorizar acceso
  - El token se guarda localmente y se renueva de forma automática
"""

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils.registro import registro
from config.ajustes import ajustes


# Permisos mínimos: lectura de correos y modificación de etiquetas
_ALCANCES_OAUTH = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class AdjuntoPDF:
    """Representa un adjunto PDF extraído de un correo de Gmail."""
    id_correo: str
    asunto: str
    remitente: str
    fecha: str
    nombre_archivo: str
    contenido_pdf: bytes


class MonitorGmail:
    """
    Monitoriza Gmail y devuelve adjuntos PDF de correos no leídos.

    Uso básico:
        monitor = MonitorGmail()
        for adjunto in monitor.obtener_nuevas_facturas():
            procesar(adjunto)
    """

    def __init__(self):
        self._servicio = self._autenticar()
        registro.info("MonitorGmail inicializado correctamente")

    # Autenticación 

    def _autenticar(self):
        """
        Gestiona el flujo OAuth2:
        - Carga el token existente si es válido
        - Lo renueva automáticamente si está caducado
        - Inicia el flujo de autorización en navegador si no existe
        """
        credenciales = None
        ruta_token = Path(ajustes.ruta_token_gmail)
        ruta_credenciales = Path(ajustes.ruta_credenciales_gmail)

        if ruta_token.exists():
            credenciales = Credentials.from_authorized_user_file(
                str(ruta_token), _ALCANCES_OAUTH
            )
            registro.debug("Token OAuth cargado desde archivo")

        if not credenciales or not credenciales.valid:
            credenciales = self._renovar_o_autorizar(credenciales, ruta_credenciales)
            ruta_token.parent.mkdir(parents=True, exist_ok=True)
            ruta_token.write_text(credenciales.to_json())
            registro.info(f"Token OAuth guardado en: {ruta_token}")

        return build("gmail", "v1", credentials=credenciales)

    def _renovar_o_autorizar(self, credenciales, ruta_credenciales: Path):
        """Renueva el token caducado o lanza el flujo de autorización inicial."""
        if credenciales and credenciales.expired and credenciales.refresh_token:
            registro.info("Renovando token OAuth caducado...")
            credenciales.refresh(Request())
            return credenciales

        if not ruta_credenciales.exists():
            raise FileNotFoundError(
                f"No se encontró el archivo de credenciales: {ruta_credenciales}\n"
                "Descárgalo desde Google Cloud Console → APIs → Credenciales"
            )

        registro.info("Iniciando autorización OAuth (se abrirá el navegador)...")
        flujo = InstalledAppFlow.from_client_secrets_file(
            str(ruta_credenciales), _ALCANCES_OAUTH
        )
        return flujo.run_local_server(port=0)

    # Búsqueda y extracción 

    def _buscar_correos_con_pdf(self) -> list[str]:
        """
        Busca correos no leídos con adjuntos PDF en la etiqueta configurada.

        Returns:
            Lista de IDs de mensajes encontrados.
        """
        consulta = "is:unread has:attachment filename:pdf"
        if ajustes.etiqueta_gmail != "INBOX":
            consulta += f" label:{ajustes.etiqueta_gmail}"

        try:
            resultado = self._servicio.users().messages().list(
                userId="me",
                q=consulta,
                maxResults=50,
            ).execute()
            mensajes = resultado.get("messages", [])
            registro.debug(f"Correos con PDF sin leer: {len(mensajes)}")
            return [m["id"] for m in mensajes]
        except HttpError as error:
            registro.error(f"Error consultando Gmail API: {error}")
            return []

    def _extraer_adjunto_pdf(self, id_mensaje: str) -> AdjuntoPDF | None:
        """
        Extrae el primer adjunto PDF de un mensaje de Gmail.

        Returns:
            AdjuntoPDF con el contenido, o None si no hay PDF adjunto.
        """
        try:
            mensaje = self._servicio.users().messages().get(
                userId="me", id=id_mensaje, format="full"
            ).execute()

            encabezados = {
                h["name"]: h["value"]
                for h in mensaje["payload"].get("headers", [])
            }
            asunto   = encabezados.get("Subject", "(sin asunto)")
            remitente = encabezados.get("From", "desconocido")
            fecha     = encabezados.get("Date", "")

            partes = self._aplanar_partes_mime(mensaje["payload"])
            for parte in partes:
                nombre_archivo = parte.get("filename", "")
                tipo_mime      = parte.get("mimeType", "")

                if not nombre_archivo.lower().endswith(".pdf") and tipo_mime != "application/pdf":
                    continue

                contenido = self._descargar_contenido_parte(id_mensaje, parte)
                if contenido is None:
                    continue

                registro.info(
                    f"PDF encontrado: '{nombre_archivo}' | "
                    f"De: {remitente} | Asunto: {asunto}"
                )
                return AdjuntoPDF(
                    id_correo=id_mensaje,
                    asunto=asunto,
                    remitente=remitente,
                    fecha=fecha,
                    nombre_archivo=nombre_archivo or "factura.pdf",
                    contenido_pdf=contenido,
                )

            registro.debug(f"Correo {id_mensaje} sin adjunto PDF válido")
            return None

        except HttpError as error:
            registro.error(f"Error obteniendo mensaje {id_mensaje}: {error}")
            return None

    def _descargar_contenido_parte(self, id_mensaje: str, parte: dict) -> bytes | None:
        """Descarga el contenido binario de una parte MIME."""
        id_adjunto = parte.get("body", {}).get("attachmentId")
        if id_adjunto:
            adjunto = self._servicio.users().messages().attachments().get(
                userId="me", messageId=id_mensaje, id=id_adjunto
            ).execute()
            return base64.urlsafe_b64decode(adjunto["data"])

        datos_inline = parte.get("body", {}).get("data", "")
        if datos_inline:
            return base64.urlsafe_b64decode(datos_inline)

        return None

    def _aplanar_partes_mime(self, carga: dict) -> list[dict]:
        """Aplana recursivamente la estructura MIME de un mensaje."""
        partes = []
        if "parts" in carga:
            for parte in carga["parts"]:
                partes.extend(self._aplanar_partes_mime(parte))
        else:
            partes.append(carga)
        return partes

    # API pública 

    def obtener_nuevas_facturas(self) -> list[AdjuntoPDF]:
        """
        Devuelve los adjuntos PDF de todos los correos no leídos encontrados.
        """
        ids_mensajes = self._buscar_correos_con_pdf()
        adjuntos = [
            adjunto
            for id_msg in ids_mensajes
            if (adjunto := self._extraer_adjunto_pdf(id_msg)) is not None
        ]
        registro.info(f"Facturas PDF encontradas: {len(adjuntos)}")
        return adjuntos

    def marcar_como_leido(self, id_mensaje: str) -> None:
        """Elimina la etiqueta UNREAD del mensaje indicado."""
        try:
            self._servicio.users().messages().modify(
                userId="me",
                id=id_mensaje,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            registro.debug(f"Mensaje {id_mensaje} marcado como leído")
        except HttpError as error:
            registro.warning(f"No se pudo marcar como leído {id_mensaje}: {error}")

    def ejecutar_en_bucle(self, manejador) -> None:
        """
        Ejecuta el monitor en bucle continuo, llamando a `manejador`
        con cada AdjuntoPDF encontrado. Bloquea hasta Ctrl+C.
        """
        registro.info(
            f"Monitor en bucle activo. "
            f"Intervalo: {ajustes.intervalo_sondeo_segundos}s"
        )
        while True:
            try:
                for adjunto in self.obtener_nuevas_facturas():
                    manejador(adjunto)
            except Exception as error:
                registro.error(f"Error en ciclo de monitoreo: {error}")
            time.sleep(ajustes.intervalo_sondeo_segundos)
