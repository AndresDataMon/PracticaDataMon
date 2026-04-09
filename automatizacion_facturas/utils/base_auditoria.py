"""
Base de datos SQLite para auditar el ciclo de vida de cada factura.

Registra cada correo procesado con su estado, hash del PDF, datos
extraídos e ID del pedido de compra creado en Odoo. Permite detectar
duplicados y consultar facturas fallidas para su reintento.
"""

import sqlite3
import hashlib
from datetime import datetime, timezone
from enum import Enum

from loguru import logger

from configuracion.ajustes import ajustes


# Estado de procesamiento

class EstadoFactura(str, Enum):
    PENDIENTE   = "pendiente"
    PROCESANDO  = "procesando"
    PROCESADA   = "procesada"
    FALLIDA     = "fallida"
    DUPLICADA   = "duplicada"
    INVALIDA    = "invalida"


# Helpers privados 

def _ahora_iso() -> str:
    """Devuelve la fecha y hora actual en UTC formato ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


# Base de datos de auditoría 

class BaseAuditoria:
    """
    Gestiona el registro de auditoría de facturas procesadas.

    Uso:
        auditoria = BaseAuditoria()
        auditoria.registrar_correo("msg_id_001")
        auditoria.actualizar_estado("msg_id_001", EstadoFactura.PROCESADA, pedido_odoo_id=42)
    """

    _ESQUEMA_SQL = """
        CREATE TABLE IF NOT EXISTS auditoria_facturas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            id_correo       TEXT    NOT NULL,
            hash_pdf        TEXT,
            estado          TEXT    NOT NULL DEFAULT 'pendiente',
            proveedor       TEXT,
            numero_factura  TEXT,
            fecha_factura   TEXT,
            importe_total   REAL,
            id_pedido_odoo  INTEGER,
            mensaje_error   TEXT,
            intentos        INTEGER DEFAULT 0,
            creado_en       TEXT    NOT NULL,
            actualizado_en  TEXT    NOT NULL
        )
    """

    def __init__(self, ruta_bd: str | None = None):
        if ruta_bd is None:
            ruta_bd = str(ajustes.obtener_directorio_datos() / "auditoria.db")
        self._ruta_bd = ruta_bd
        self._inicializar_esquema()

    # Conexión 

    def _conexion(self) -> sqlite3.Connection:
        conexion = sqlite3.connect(self._ruta_bd)
        conexion.row_factory = sqlite3.Row
        return conexion

    def _inicializar_esquema(self) -> None:
        """Crea las tablas e índices si no existen todavía."""
        with self._conexion() as con:
            con.execute(self._ESQUEMA_SQL)
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_id_correo "
                "ON auditoria_facturas(id_correo)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hash_pdf "
                "ON auditoria_facturas(hash_pdf)"
            )
        logger.debug(f"Base de auditoría lista: {self._ruta_bd}")

    # Operaciones públicas 

    def registrar_correo(self, id_correo: str) -> bool:
        """
        Registra un correo nuevo en la auditoría.

        Returns:
            True si era nuevo y se insertó; False si ya existía.
        """
        ahora = _ahora_iso()
        try:
            with self._conexion() as con:
                con.execute(
                    "INSERT INTO auditoria_facturas "
                    "(id_correo, estado, creado_en, actualizado_en) "
                    "VALUES (?, ?, ?, ?)",
                    (id_correo, EstadoFactura.PENDIENTE, ahora, ahora),
                )
            logger.debug(f"Correo registrado en auditoría: {id_correo}")
            return True
        except sqlite3.IntegrityError:
            logger.debug(f"Correo ya existente en auditoría: {id_correo}")
            return False

    def es_pdf_duplicado(self, contenido_pdf: bytes) -> bool:
        """
        Comprueba si el hash SHA-256 del PDF ya fue procesado con éxito.

        Returns:
            True si el PDF ya existe en estado 'procesada'.
        """
        hash_pdf = hashlib.sha256(contenido_pdf).hexdigest()
        with self._conexion() as con:
            fila = con.execute(
                "SELECT id FROM auditoria_facturas "
                "WHERE hash_pdf = ? AND estado = 'procesada'",
                (hash_pdf,),
            ).fetchone()
        return fila is not None

    def actualizar_estado(
        self,
        id_correo: str,
        estado: EstadoFactura,
        *,
        hash_pdf: str | None            = None,
        proveedor: str | None           = None,
        numero_factura: str | None      = None,
        fecha_factura: str | None       = None,
        importe_total: float | None     = None,
        id_pedido_odoo: int | None      = None,
        mensaje_error: str | None       = None,
    ) -> None:
        """Actualiza el estado y los metadatos de una factura en la auditoría."""
        ahora = _ahora_iso()
        with self._conexion() as con:
            con.execute(
                """UPDATE auditoria_facturas SET
                    estado          = ?,
                    hash_pdf        = COALESCE(?, hash_pdf),
                    proveedor       = COALESCE(?, proveedor),
                    numero_factura  = COALESCE(?, numero_factura),
                    fecha_factura   = COALESCE(?, fecha_factura),
                    importe_total   = COALESCE(?, importe_total),
                    id_pedido_odoo  = COALESCE(?, id_pedido_odoo),
                    mensaje_error   = COALESCE(?, mensaje_error),
                    intentos        = intentos + 1,
                    actualizado_en  = ?
                WHERE id_correo = ?""",
                (
                    estado, hash_pdf, proveedor, numero_factura,
                    fecha_factura, importe_total, id_pedido_odoo,
                    mensaje_error, ahora, id_correo,
                ),
            )

    def obtener_facturas_fallidas(self, max_intentos: int = 3) -> list[dict]:
        """Devuelve facturas fallidas que aún no han agotado sus reintentos."""
        with self._conexion() as con:
            filas = con.execute(
                "SELECT * FROM auditoria_facturas "
                "WHERE estado = 'fallida' AND intentos < ? "
                "ORDER BY creado_en ASC",
                (max_intentos,),
            ).fetchall()
        return [dict(fila) for fila in filas]

    def obtener_estadisticas(self) -> dict:
        """Devuelve el recuento de facturas agrupado por estado."""
        with self._conexion() as con:
            filas = con.execute(
                "SELECT estado, COUNT(*) AS total "
                "FROM auditoria_facturas GROUP BY estado"
            ).fetchall()
        return {fila["estado"]: fila["total"] for fila in filas}
