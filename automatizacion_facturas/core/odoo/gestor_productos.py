"""
Gestiona la resolución y creación de productos en Odoo con búsqueda
inteligente en tres pasos:

  1. Buscar por nombre exacto (insensible a mayúsculas)
  2. Si no aparece, buscar por código de barras
  3. Solo si no existe por ninguna vía → crear el producto

Diseñado para inyección de dependencias: recibe el callable de Odoo
en lugar de gestionar su propia conexión, facilitando los tests.
"""

from __future__ import annotations

import xmlrpc.client
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from loguru import logger


# Enumerados

class TipoProducto(str, Enum):
    """Tipos de producto válidos en Odoo."""
    CONSUMIBLE  = "consu"    # Producto físico con stock
    SERVICIO    = "service"  # Servicio sin movimiento de stock
    ALMACENABLE = "product"  # Almacenable (Odoo ≥ 17: "storable")


class OrigenResolucion(str, Enum):
    """Indica cómo se resolvió un producto."""
    ENCONTRADO_POR_NOMBRE    = "encontrado_por_nombre"
    ENCONTRADO_POR_CODIGO    = "encontrado_por_codigo_barras"
    CREADO                   = "creado"


# Campos recuperados de Odoo al leer un producto
_CAMPOS_LECTURA = ["id", "name", "barcode", "list_price", "type", "active"]


# Modelos de datos

@dataclass
class DatosProducto:
    """
    Datos de entrada para resolver o crear un producto en Odoo.

    Campos obligatorios: nombre
    Campos opcionales:   codigo_barras, precio, tipo, comprable, vendible,
                         descripcion, referencia_interna
    """
    nombre:              str
    codigo_barras:       Optional[str]   = None
    precio:              Optional[float] = None
    tipo:                TipoProducto    = TipoProducto.SERVICIO
    comprable:           bool            = True
    vendible:            bool            = False
    descripcion:         Optional[str]   = None
    referencia_interna:  Optional[str]   = None

    def __post_init__(self) -> None:
        """Normaliza los campos de texto eliminando espacios superfluos."""
        self.nombre = self.nombre.strip()
        if self.codigo_barras:
            self.codigo_barras = self.codigo_barras.strip()
        if self.referencia_interna:
            self.referencia_interna = self.referencia_interna.strip()


@dataclass
class ResultadoProducto:
    """
    Resultado de una operación de resolución de producto.

    Attributes:
        id_producto: ID del registro en Odoo (product.product)
        origen:      Cómo se resolvió el producto
        datos_odoo:  Campos del producto tal como existen en Odoo
    """
    id_producto: int
    origen:      OrigenResolucion
    datos_odoo:  dict = field(default_factory=dict)

    @property
    def fue_creado(self) -> bool:
        """True si el producto se creó en esta operación."""
        return self.origen == OrigenResolucion.CREADO

    @property
    def fue_encontrado(self) -> bool:
        """True si el producto ya existía en Odoo."""
        return not self.fue_creado


# Excepciones

class ErrorValidacionProducto(ValueError):
    """Los datos de entrada del producto no superan la validación."""
    pass


class ErrorApiOdoo(RuntimeError):
    """Error en la comunicación con la API de Odoo."""
    pass


# Validador de entrada

class ValidadorDatosProducto:
    """
    Valida los datos de entrada de un producto antes de consultar Odoo.
    Separado del gestor para poder usarse de forma independiente.
    """

    LONGITUD_MAX_NOMBRE    = 250
    LONGITUD_MAX_BARCODE   = 100
    LONGITUD_MAX_REFERENCIA = 64

    def validar(self, datos: DatosProducto) -> list[str]:
        """
        Valida el objeto DatosProducto.

        Returns:
            Lista de mensajes de error. Lista vacía = datos válidos.
        """
        errores: list[str] = []
        self._validar_nombre(datos, errores)
        self._validar_precio(datos, errores)
        self._validar_codigo_barras(datos, errores)
        self._validar_referencia_interna(datos, errores)
        self._validar_tipo(datos, errores)
        return errores

    def validar_o_lanzar(self, datos: DatosProducto) -> None:
        """Valida y lanza ErrorValidacionProducto si hay errores."""
        errores = self.validar(datos)
        if errores:
            detalle = "\n".join(f"  • {e}" for e in errores)
            raise ErrorValidacionProducto(
                f"Datos de producto inválidos ({len(errores)} error(es)):\n{detalle}"
            )

    # Validaciones individuales 

    def _validar_nombre(self, datos: DatosProducto, errores: list[str]) -> None:
        if not datos.nombre:
            errores.append("El nombre del producto es obligatorio.")
        elif len(datos.nombre) > self.LONGITUD_MAX_NOMBRE:
            errores.append(
                f"El nombre excede {self.LONGITUD_MAX_NOMBRE} caracteres "
                f"(longitud actual: {len(datos.nombre)})."
            )

    def _validar_precio(self, datos: DatosProducto, errores: list[str]) -> None:
        if datos.precio is None:
            return
        if not isinstance(datos.precio, (int, float)):
            errores.append("El precio debe ser un número.")
        elif datos.precio < 0:
            errores.append(f"El precio no puede ser negativo ({datos.precio}).")

    def _validar_codigo_barras(self, datos: DatosProducto, errores: list[str]) -> None:
        if datos.codigo_barras is None:
            return
        if len(datos.codigo_barras) == 0:
            errores.append("El código de barras no puede estar vacío.")
        elif len(datos.codigo_barras) > self.LONGITUD_MAX_BARCODE:
            errores.append(
                f"El código de barras excede {self.LONGITUD_MAX_BARCODE} caracteres "
                f"(longitud actual: {len(datos.codigo_barras)})."
            )
        elif not datos.codigo_barras.replace("-", "").replace(" ", "").isalnum():
            errores.append(
                f"El código de barras contiene caracteres no válidos: "
                f"'{datos.codigo_barras}'."
            )

    def _validar_referencia_interna(self, datos: DatosProducto, errores: list[str]) -> None:
        if datos.referencia_interna and len(datos.referencia_interna) > self.LONGITUD_MAX_REFERENCIA:
            errores.append(
                f"La referencia interna excede {self.LONGITUD_MAX_REFERENCIA} caracteres."
            )

    def _validar_tipo(self, datos: DatosProducto, errores: list[str]) -> None:
        if not isinstance(datos.tipo, TipoProducto):
            valores_validos = [t.value for t in TipoProducto]
            errores.append(
                f"Tipo de producto inválido: '{datos.tipo}'. "
                f"Valores aceptados: {valores_validos}."
            )


# Gestor principal

class GestorProductos:
    """
    Resuelve y crea productos en Odoo con búsqueda inteligente.

    No gestiona su propia conexión: recibe `llamada_odoo` como callable,
    lo que permite usarlo con cualquier cliente Odoo y facilita los tests.

    Args:
        llamada_odoo: callable con firma
                      (modelo, metodo, args, kwargs) -> Any
                      Normalmente es el método `_llamar` de ConectorOdoo.

    Uso:
        gestor = GestorProductos(conector._llamar)
        resultado = gestor.resolver_producto(DatosProducto(nombre="MIMOSA"))
    """

    def __init__(self, llamada_odoo: Callable) -> None:
        self._llamar    = llamada_odoo
        self._validador = ValidadorDatosProducto()

    # API pública

    def resolver_producto(self, datos: DatosProducto) -> ResultadoProducto:
        """
        Resuelve un producto en Odoo siguiendo este orden:
          1. Validar datos de entrada
          2. Buscar por nombre exacto (insensible a mayúsculas)
          3. Buscar por código de barras (si está disponible)
          4. Crear el producto si no se encontró

        Returns:
            ResultadoProducto con el ID y el origen de la resolución.

        Raises:
            ErrorValidacionProducto: datos de entrada inválidos.
            ErrorApiOdoo: error de comunicación con Odoo.
        """
        self._validador.validar_o_lanzar(datos)

        logger.info(
            f"Resolviendo producto: '{datos.nombre}'"
            + (f" | código: {datos.codigo_barras}" if datos.codigo_barras else "")
        )

        producto = self._buscar_por_nombre(datos.nombre)
        if producto:
            logger.info(
                f"Producto encontrado por nombre: '{producto['name']}' "
                f"(ID: {producto['id']})"
            )
            return ResultadoProducto(
                id_producto=producto["id"],
                origen=OrigenResolucion.ENCONTRADO_POR_NOMBRE,
                datos_odoo=producto,
            )

        if datos.codigo_barras:
            producto = self._buscar_por_codigo_barras(datos.codigo_barras)
            if producto:
                logger.info(
                    f"Producto encontrado por código '{datos.codigo_barras}': "
                    f"'{producto['name']}' (ID: {producto['id']})"
                )
                return ResultadoProducto(
                    id_producto=producto["id"],
                    origen=OrigenResolucion.ENCONTRADO_POR_CODIGO,
                    datos_odoo=producto,
                )

        logger.info(f"Producto '{datos.nombre}' no existe en Odoo. Creando...")
        resultado = self._crear_producto(datos)
        if resultado.fue_creado:
            logger.success(f"Producto creado: '{datos.nombre}' (ID: {resultado.id_producto})")
        return resultado

    # Búsquedas

    def _buscar_por_nombre(self, nombre: str) -> Optional[dict]:
        """
        Busca un producto activo por nombre exacto (insensible a mayúsculas).
        Usa =ilike: igualdad exacta pero case-insensitive.
        """
        try:
            ids = self._llamar(
                "product.product",
                "search",
                [[["name", "=ilike", nombre], ["active", "=", True]]],
                {"limit": 1},
            )
        except xmlrpc.client.Fault as error:
            raise ErrorApiOdoo(
                f"Error buscando por nombre '{nombre}': {error.faultString}"
            ) from error
        return self._leer_producto(ids[0]) if ids else None

    def _buscar_por_codigo_barras(self, codigo: str) -> Optional[dict]:
        """Busca un producto activo por código de barras exacto."""
        try:
            ids = self._llamar(
                "product.product",
                "search",
                [[["barcode", "=", codigo], ["active", "=", True]]],
                {"limit": 1},
            )
        except xmlrpc.client.Fault as error:
            raise ErrorApiOdoo(
                f"Error buscando por código '{codigo}': {error.faultString}"
            ) from error
        return self._leer_producto(ids[0]) if ids else None

    def _leer_producto(self, id_producto: int) -> dict:
        """Lee los campos estándar de un producto por su ID."""
        try:
            registros = self._llamar(
                "product.product",
                "read",
                [[id_producto]],
                {"fields": _CAMPOS_LECTURA},
            )
        except xmlrpc.client.Fault as error:
            raise ErrorApiOdoo(
                f"Error leyendo producto ID {id_producto}: {error.faultString}"
            ) from error

        if not registros:
            raise ErrorApiOdoo(
                f"Producto ID {id_producto} no encontrado tras su creación."
            )
        return registros[0]

    # Creación

    def _construir_valores_creacion(self, datos: DatosProducto) -> dict:
        """
        Construye el diccionario de campos para la llamada create de Odoo.
        Solo incluye campos con valor para no sobrescribir defaults de Odoo.
        """
        valores: dict = {
            "name":        datos.nombre,
            "type":        datos.tipo.value,
            "purchase_ok": datos.comprable,
            "sale_ok":     datos.vendible,
        }
        if datos.precio is not None:
            valores["list_price"] = round(float(datos.precio), 6)
        if datos.codigo_barras:
            valores["barcode"] = datos.codigo_barras
        if datos.descripcion:
            valores["description"] = datos.descripcion
        if datos.referencia_interna:
            valores["default_code"] = datos.referencia_interna
        return valores

    def _crear_producto(self, datos: DatosProducto) -> ResultadoProducto:
        """
        Crea el producto en Odoo y devuelve un ResultadoProducto.

        Gestiona la race condition de barcode duplicado: si create falla
        por restricción unique, reintenta la búsqueda por código de barras
        y devuelve el producto existente con origen ENCONTRADO_POR_CODIGO.

        Raises:
            ErrorApiOdoo: si la API devuelve un error no recuperable.
        """
        valores = self._construir_valores_creacion(datos)
        try:
            id_nuevo = self._llamar("product.product", "create", [valores])
        except xmlrpc.client.Fault as error:
            error_texto = error.faultString.lower()
            if datos.codigo_barras and ("barcode" in error_texto or "unique" in error_texto):
                return self._recuperar_por_barcode_duplicado(datos.codigo_barras)
            raise ErrorApiOdoo(
                f"Error creando producto '{datos.nombre}': {error.faultString}"
            ) from error

        if not isinstance(id_nuevo, int) or id_nuevo <= 0:
            raise ErrorApiOdoo(
                f"Odoo devolvió un ID inválido al crear '{datos.nombre}': {id_nuevo!r}"
            )

        datos_creados = self._leer_producto(id_nuevo)
        return ResultadoProducto(
            id_producto=id_nuevo,
            origen=OrigenResolucion.CREADO,
            datos_odoo=datos_creados,
        )

    def _recuperar_por_barcode_duplicado(self, codigo: str) -> ResultadoProducto:
        """
        Fallback ante race condition: otro proceso creó el producto
        justo antes que nosotros. Buscamos por código y devolvemos el existente.
        """
        logger.warning(
            f"Código de barras '{codigo}' duplicado al crear. "
            "Recuperando producto existente..."
        )
        producto = self._buscar_por_codigo_barras(codigo)
        if producto:
            return ResultadoProducto(
                id_producto=producto["id"],
                origen=OrigenResolucion.ENCONTRADO_POR_CODIGO,
                datos_odoo=producto,
            )
        raise ErrorApiOdoo(
            f"Código de barras '{codigo}' duplicado pero no se encontró el producto."
        )
