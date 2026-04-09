"""
Orquestador del pipeline completo de procesamiento de facturas.
 
Coordina el flujo: Correo → PDF → Gemini → Validación → Odoo
y delega en cada módulo especializado su responsabilidad concreta.
 
Modos de uso:
  - orquestador.procesar_bytes_pdf(bytes)  → procesa un PDF directamente
  - orquestador.procesar_archivo_pdf(ruta) → procesa un PDF desde disco
  - orquestador.ejecutar()                  → monitoriza Gmail en bucle
 
Gestión de errores en el bucle de Gmail:
  - Éxito                → email marcado como leído
  - Fallo permanente     → email marcado como leído (no se reintentará)
      · GeminiQuotaError   (sin cuota)
      · GeminiModelError   (modelo no disponible)
      · Validación fallida (datos extraídos pero inválidos)
  - Fallo transitorio    → email NO marcado (se reintentará en el próximo ciclo)
      · TimeoutError / ConnectionError
"""
 
import hashlib
from pathlib import Path
 
from utils.registro import registro
from utils.base_auditoria import BaseAuditoria, EstadoFactura
from core.inteligencia_artificial.extractor_gemini import (
    ExtractorGemini,
    GeminiErrorPermanente,
)
from core.validator.validador_facturas import ValidadorFacturas
from core.odoo.conector_odoo import ConectorOdoo
 
# Clave interna usada en el dict resultado para señalar fallos permanentes
_FALLO_PERMANENTE = "_fallo_permanente"
 
 
class OrquestadorFacturas:
    """
    Orquesta el pipeline completo de procesamiento de facturas.
 
    Uso con Gmail (producción):
        orquestador = OrquestadorFacturas()
        orquestador.ejecutar()
 
    Uso con PDF local (pruebas):
        orquestador = OrquestadorFacturas(sin_odoo=True)
        resultado = orquestador.procesar_archivo_pdf("factura.pdf")
    """
 
    def __init__(self, sin_odoo: bool = False):
        """
        Args:
            sin_odoo: si True, no conecta a Odoo (útil para pruebas y depuración).
        """
        registro.info("Iniciando OrquestadorFacturas...")
        self._extractor = ExtractorGemini()
        self._validador = ValidadorFacturas()
        self._auditoria = BaseAuditoria()
        self._odoo      = self._iniciar_odoo(sin_odoo)
        registro.info("Orquestador listo.")
 
 
    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------
 
    @staticmethod
    def _iniciar_odoo(sin_odoo: bool) -> ConectorOdoo | None:
        """Intenta conectar a Odoo. Si falla, continúa sin integración."""
        if sin_odoo:
            return None
        try:
            return ConectorOdoo()
        except Exception as error:
            registro.warning(
                f"No se pudo conectar a Odoo: {error}\n"
                "El pipeline continuará sin crear pedidos (modo validación)."
            )
            return None
 
 
    # ------------------------------------------------------------------
    # Procesamiento de facturas
    # ------------------------------------------------------------------
 
    def procesar_bytes_pdf(
        self,
        contenido_pdf: bytes,
        id_correo: str = "manual",
        nombre_archivo: str = "factura.pdf",
    ) -> dict:
        """
        Procesa un PDF de factura ejecutando el pipeline completo.
 
        Args:
            contenido_pdf:  bytes del archivo PDF.
            id_correo:      identificador del correo de origen (para auditoría).
            nombre_archivo: nombre del fichero PDF.
 
        Returns:
            Diccionario con las claves:
              - exitoso            (bool)
              - id_pedido          (int | None): ID del pedido en Odoo
              - datos_factura      (dict | None): campos extraídos
              - errores            (list[str]): mensajes de error
              - _fallo_permanente  (bool): True si el error no tiene sentido reintentar
        """
        resultado = {
            "exitoso": False,
            "id_pedido": None,
            "datos_factura": None,
            "errores": [],
            _FALLO_PERMANENTE: False,
        }
        hash_pdf = hashlib.sha256(contenido_pdf).hexdigest()
 
        if self._es_duplicado(contenido_pdf, id_correo):
            resultado["errores"].append("PDF ya procesado anteriormente (duplicado).")
            resultado[_FALLO_PERMANENTE] = True  # duplicado → no reintentar
            return resultado
 
        self._auditoria.registrar_correo(id_correo)
        self._auditoria.actualizar_estado(
            id_correo, EstadoFactura.PROCESANDO, hash_pdf=hash_pdf
        )
 
        datos_crudos = self._paso_extraccion(id_correo, contenido_pdf, resultado)
        if datos_crudos is None:
            return resultado
 
        factura = self._paso_validacion(id_correo, datos_crudos, resultado)
        if factura is None:
            return resultado
 
        resultado["datos_factura"] = datos_crudos
        self._paso_odoo(id_correo, factura, resultado)
        return resultado
 
    def procesar_archivo_pdf(self, ruta_pdf: str | Path) -> dict:
        """Procesa un PDF almacenado en disco. Útil para pruebas manuales."""
        ruta = Path(ruta_pdf)
        if not ruta.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {ruta}")
        registro.info(f"Procesando archivo: {ruta.name}")
        return self.procesar_bytes_pdf(
            contenido_pdf=ruta.read_bytes(),
            id_correo=f"archivo:{ruta.stem}",
            nombre_archivo=ruta.name,
        )
 
 
    # ------------------------------------------------------------------
    # Pasos del pipeline
    # ------------------------------------------------------------------
 
    def _es_duplicado(self, contenido_pdf: bytes, id_correo: str) -> bool:
        """Devuelve True si el PDF ya fue procesado con éxito anteriormente."""
        if self._auditoria.es_pdf_duplicado(contenido_pdf):
            hash_corto = hashlib.sha256(contenido_pdf).hexdigest()[:8]
            registro.warning(
                f"PDF duplicado detectado (hash: {hash_corto}...). Omitiendo."
            )
            self._auditoria.actualizar_estado(id_correo, EstadoFactura.DUPLICADA)
            return True
        return False
 
    def _paso_extraccion(
        self, id_correo: str, contenido_pdf: bytes, resultado: dict
    ) -> dict | None:
        """
        Paso 1: extraer datos del PDF con Gemini.
 
        - GeminiErrorPermanente (sin cuota, modelo no disponible):
            marca _fallo_permanente=True para que el orquestador
            descarte el email sin reintentarlo.
        - Cualquier otro error:
            fallo transitorio, el email se reintentará en el próximo ciclo.
        """
        registro.info(f"[{id_correo}] Paso 1/3: Extrayendo datos con Gemini...")
        try:
            return self._extractor.extraer_datos_factura(contenido_pdf)
 
        except GeminiErrorPermanente as error:
            mensaje = f"Error permanente en Gemini: {error}"
            registro.error(f"[{id_correo}] {mensaje}")
            self._auditoria.actualizar_estado(
                id_correo, EstadoFactura.FALLIDA, mensaje_error=mensaje
            )
            resultado["errores"].append(mensaje)
            resultado[_FALLO_PERMANENTE] = True
            return None
 
        except Exception as error:
            mensaje = f"Error en extracción Gemini: {error}"
            registro.error(f"[{id_correo}] {mensaje}")
            self._auditoria.actualizar_estado(
                id_correo, EstadoFactura.FALLIDA, mensaje_error=mensaje
            )
            resultado["errores"].append(mensaje)
            # _fallo_permanente queda False → el email se reintentará
            return None
 
    def _paso_validacion(
        self, id_correo: str, datos_crudos: dict, resultado: dict
    ):
        """
        Paso 2: validar los datos extraídos contra el esquema Pydantic.
 
        Un fallo de validación es permanente: Gemini ya procesó el PDF
        y los datos no son válidos. Marcar como permanente evita el bucle.
        """
        registro.info(f"[{id_correo}] Paso 2/3: Validando datos...")
        validacion = self._validador.validar(datos_crudos)
        if not validacion.es_valido:
            mensaje = f"Validación fallida: {'; '.join(validacion.errores)}"
            registro.warning(f"[{id_correo}] {mensaje}")
            self._auditoria.actualizar_estado(
                id_correo, EstadoFactura.INVALIDA, mensaje_error=mensaje
            )
            resultado["errores"].extend(validacion.errores)
            resultado["datos_factura"] = datos_crudos
            resultado[_FALLO_PERMANENTE] = True  # no reintentar
            return None
        return validacion.factura
 
    def _paso_odoo(self, id_correo: str, factura, resultado: dict) -> None:
        """Paso 3: crear el pedido de compra en Odoo."""
        if self._odoo:
            self._crear_pedido_con_auditoria(id_correo, factura, resultado)
        else:
            self._registrar_exito_sin_odoo(id_correo, factura, resultado)
 
    def _crear_pedido_con_auditoria(
        self, id_correo: str, factura, resultado: dict
    ) -> None:
        """Crea el pedido en Odoo y actualiza la auditoría según el resultado."""
        registro.info(f"[{id_correo}] Paso 3/3: Creando pedido en Odoo...")
        try:
            id_pedido = self._odoo.crear_pedido_compra(factura)
            resultado["id_pedido"] = id_pedido
            resultado["exitoso"]   = True
            self._auditoria.actualizar_estado(
                id_correo,
                EstadoFactura.PROCESADA,
                proveedor=factura.proveedor,
                numero_factura=factura.numero_factura,
                fecha_factura=str(factura.fecha),
                importe_total=factura.total,
                id_pedido_odoo=id_pedido,
            )
            registro.success(
                f"[{id_correo}] ✓ Factura procesada. Pedido Odoo: {id_pedido}"
            )
        except Exception as error:
            mensaje = f"Error creando pedido en Odoo: {error}"
            registro.error(f"[{id_correo}] {mensaje}")
            self._auditoria.actualizar_estado(
                id_correo, EstadoFactura.FALLIDA, mensaje_error=mensaje
            )
            resultado["errores"].append(mensaje)
            # Error de Odoo es transitorio → no marcar como permanente
 
    def _registrar_exito_sin_odoo(
        self, id_correo: str, factura, resultado: dict
    ) -> None:
        """Registra el éxito cuando no hay conexión a Odoo configurada."""
        resultado["exitoso"] = True
        self._auditoria.actualizar_estado(
            id_correo,
            EstadoFactura.PROCESADA,
            proveedor=factura.proveedor,
            numero_factura=factura.numero_factura,
            fecha_factura=str(factura.fecha),
            importe_total=factura.total,
        )
        registro.success(
            f"[{id_correo}] ✓ Factura validada (sin Odoo). "
            f"Proveedor: {factura.proveedor} | Total: {factura.total}€"
        )
 
 
    # ------------------------------------------------------------------
    # Monitor continuo
    # ------------------------------------------------------------------
 
    def ejecutar(self) -> None:
        """
        Arranca el monitor de Gmail en bucle continuo.
        Bloquea hasta recibir Ctrl+C.
 
        Lógica de marcado de email:
          - Éxito                → marcar como leído
          - Fallo permanente     → marcar como leído (sin cuota, modelo no
                                   disponible, validación fallida, duplicado)
          - Fallo transitorio    → NO marcar (timeout, red, Odoo caído)
                                   → se reintentará en el próximo ciclo
        """
        from core.mail.monitor_gmail import MonitorGmail
 
        monitor = MonitorGmail()
        registro.info("Orquestador en modo continuo. Pulsa Ctrl+C para detener.")
 
        def procesar_adjunto(adjunto):
            registro.info(
                f"Procesando: '{adjunto.nombre_archivo}' | "
                f"De: {adjunto.remitente} | "
                f"Asunto: {adjunto.asunto}"
            )
            resultado = self.procesar_bytes_pdf(
                contenido_pdf=adjunto.contenido_pdf,
                id_correo=adjunto.id_correo,
                nombre_archivo=adjunto.nombre_archivo,
            )
 
            if resultado["exitoso"]:
                monitor.marcar_como_leido(adjunto.id_correo)
                registro.info(
                    f"[{adjunto.id_correo}] Email marcado como leído (procesado con éxito)."
                )
 
            elif resultado.get(_FALLO_PERMANENTE):
                monitor.marcar_como_leido(adjunto.id_correo)
                registro.warning(
                    f"[{adjunto.id_correo}] Email marcado como leído "
                    f"(fallo permanente, no se reintentará). "
                    f"Errores: {resultado['errores']}"
                )
 
            else:
                registro.warning(
                    f"[{adjunto.id_correo}] Email NO marcado como leído "
                    f"(fallo transitorio, se reintentará en el próximo ciclo). "
                    f"Errores: {resultado['errores']}"
                )
 
            return resultado
 
        monitor.ejecutar_en_bucle(procesar_adjunto)
 
 
    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
 
    def obtener_estadisticas(self) -> dict:
        """Devuelve el recuento de facturas procesadas por estado."""
        return self._auditoria.obtener_estadisticas()