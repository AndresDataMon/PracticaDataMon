"""
Cliente XML-RPC para Odoo. Gestiona la autenticación y expone
métodos de alto nivel para proveedores y pedidos de compra.
 
Integra el GestorProductos mediante una propiedad lazy, reutilizando
la misma sesión autenticada para todas las operaciones.
"""
 
import xmlrpc.client
from functools import wraps
from socket import gaierror
 
from utils.registro import registro
from config.ajustes import ajustes
from core.validator.esquema_factura import Factura
from core.odoo.gestor_productos import (
    GestorProductos,
    DatosProducto,
    TipoProducto,
    ErrorValidacionProducto,
    ErrorApiOdoo,
)
 
 
# ---------------------------------------------------------------------------
# Decorador de errores de conexión
# ---------------------------------------------------------------------------
 
def _manejar_errores_odoo(funcion):
    """
    Decorador que captura los errores más comunes de la API de Odoo
    y los convierte en RuntimeError con mensajes descriptivos.
    """
    @wraps(funcion)
    def envoltura(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except xmlrpc.client.Fault as error:
            registro.error(f"Error XML-RPC Odoo: {error.faultCode} - {error.faultString}")
            raise RuntimeError(f"Error en Odoo: {error.faultString}") from error
        except (gaierror, ConnectionRefusedError) as error:
            registro.error(f"Sin conexión a Odoo ({ajustes.url_odoo}): {error}")
            raise RuntimeError(
                f"No se puede conectar a Odoo. "
                f"Verifica URL_ODOO en .env: {ajustes.url_odoo}"
            ) from error
    return envoltura
 
 
# ---------------------------------------------------------------------------
# Conector principal
# ---------------------------------------------------------------------------
 
class ConectorOdoo:
    """
    Cliente XML-RPC para Odoo con métodos de alto nivel.
 
    Gestiona la sesión autenticada y expone operaciones para:
      - Proveedores (res.partner)
      - Productos (product.product) — vía GestorProductos
      - Pedidos de compra (purchase.order)
 
    Uso:
        conector = ConectorOdoo()
        id_pedido = conector.crear_pedido_compra(factura)
    """
 
    # Campo de notas en purchase.order según versión de Odoo:
    # Odoo 16-17: "notes"  |  Odoo 18-19: "note"
    # Se detecta automáticamente en _detectar_campo_notas().
    _CAMPO_NOTAS_PEDIDO: str = "note"
 
    def __init__(self):
        self._url     = ajustes.url_odoo.rstrip("/")
        self._bd      = ajustes.base_datos_odoo
        self._comun   = xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/common")
        self._modelos = xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/object")
        self._uid     = None
        self._autenticar()
        self._detectar_campo_notas()
 
 
    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------
 
    @_manejar_errores_odoo
    def _autenticar(self) -> None:
        """Autentica con Odoo y almacena el UID de la sesión."""
        self._uid = self._comun.authenticate(
            self._bd,
            ajustes.usuario_odoo,
            ajustes.contrasena_odoo,
            {},
        )
        if not self._uid:
            raise RuntimeError(
                "Autenticación en Odoo fallida. "
                "Verifica USUARIO_ODOO y CONTRASENA_ODOO en .env"
            )
        registro.info(
            f"Conectado a Odoo: {self._url} | "
            f"BD: {self._bd} | UID: {self._uid}"
        )
 
    def _detectar_campo_notas(self) -> None:
        """
        Detecta si el modelo purchase.order usa 'note' o 'notes'
        según la versión de Odoo instalada, y lo almacena en
        _CAMPO_NOTAS_PEDIDO para usarlo en búsquedas y creación.
        """
        try:
            campos = self._llamar(
                "purchase.order",
                "fields_get",
                [],
                {"attributes": ["type"]},
            )
            if "note" in campos:
                self._CAMPO_NOTAS_PEDIDO = "note"
            elif "notes" in campos:
                self._CAMPO_NOTAS_PEDIDO = "notes"
            else:
                # Fallback: no usar notas en la búsqueda de duplicados
                self._CAMPO_NOTAS_PEDIDO = None
 
            registro.info(
                f"Campo de notas en purchase.order: "
                f"'{self._CAMPO_NOTAS_PEDIDO or 'no disponible'}'"
            )
        except Exception as error:
            registro.warning(
                f"No se pudo detectar campo de notas en purchase.order: {error}. "
                "Se usará 'note' por defecto."
            )
            self._CAMPO_NOTAS_PEDIDO = "note"
 
 
    # ------------------------------------------------------------------
    # Llamada genérica a la API
    # ------------------------------------------------------------------
 
    def _llamar(
        self,
        modelo: str,
        metodo: str,
        argumentos: list,
        opciones: dict | None = None,
    ):
        """Wrapper de execute_kw. Punto único de llamada a la API de Odoo."""
        return self._modelos.execute_kw(
            self._bd,
            self._uid,
            ajustes.contrasena_odoo,
            modelo,
            metodo,
            argumentos,
            opciones or {},
        )
 
 
    # ------------------------------------------------------------------
    # Gestor de productos (lazy)
    # ------------------------------------------------------------------
 
    @property
    def productos(self) -> GestorProductos:
        """
        Acceso al GestorProductos. Se instancia una única vez
        y reutiliza la sesión del conector.
        """
        if not hasattr(self, "_gestor_productos"):
            self._gestor_productos = GestorProductos(self._llamar)
        return self._gestor_productos
 
 
    # ------------------------------------------------------------------
    # Gestión de proveedores
    # ------------------------------------------------------------------
 
    def obtener_o_crear_proveedor(self, factura: Factura) -> int:
        """
        Busca un proveedor por CIF/NIF en Odoo. Si no existe, lo crea.
 
        Returns:
            ID del registro res.partner en Odoo.
        """
        # Buscar por CIF con supplier_rank > 0
        ids_proveedor = self._llamar(
            "res.partner",
            "search",
            [[["vat", "=", factura.cif], ["supplier_rank", ">", 0]]],
            {"limit": 1},
        )
 
        # Si no encontrado por CIF+supplier, intentar solo por CIF
        if not ids_proveedor:
            ids_proveedor = self._llamar(
                "res.partner",
                "search",
                [[["vat", "=", factura.cif]]],
                {"limit": 1},
            )
 
        if ids_proveedor:
            datos_proveedor = self._llamar(
                "res.partner",
                "read",
                [ids_proveedor],
                {"fields": ["id", "name", "vat"]},
            )[0]
            registro.info(
                f"Proveedor encontrado: {datos_proveedor['name']} "
                f"(CIF: {datos_proveedor['vat']}, ID: {datos_proveedor['id']})"
            )
            return datos_proveedor["id"]
 
        registro.info(
            f"Proveedor no encontrado. Creando: {factura.proveedor} ({factura.cif})"
        )
 
        datos_proveedor = {
            "name":          factura.proveedor,
            "vat":           factura.cif,
            "supplier_rank": 1,
            "is_company":    True,
            "comment":       "Creado automáticamente por automatizacion_facturas",
        }
        # street es opcional: solo añadir si hay dirección
        if factura.direccion_proveedor:
            datos_proveedor["street"] = factura.direccion_proveedor
 
        id_nuevo = self._llamar("res.partner", "create", [datos_proveedor])
        registro.success(f"Proveedor creado con ID: {id_nuevo}")
        return id_nuevo
 
 
    # ------------------------------------------------------------------
    # Gestión de pedidos de compra
    # ------------------------------------------------------------------
 
    def _buscar_pedido_existente(self, factura: Factura, id_proveedor: int) -> int | None:
        """
        Verifica si ya existe un pedido de compra para esta factura.
        Garantiza la idempotencia: no se crean pedidos duplicados.
 
        Estrategia de búsqueda:
          1. Por proveedor + número de factura en el campo de notas (si existe)
          2. Por proveedor + fecha + importe total (fallback sin notas)
 
        Returns:
            ID del pedido existente, o None si no existe.
        """
        # Estrategia 1: buscar por notas si el campo existe
        if self._CAMPO_NOTAS_PEDIDO:
            try:
                ids_pedido = self._llamar(
                    "purchase.order",
                    "search",
                    [[
                        ["partner_id", "=", id_proveedor],
                        [self._CAMPO_NOTAS_PEDIDO, "ilike", factura.numero_factura],
                    ]],
                    {"limit": 1},
                )
                if ids_pedido:
                    registro.warning(
                        f"Pedido ya existente para factura {factura.numero_factura}: "
                        f"ID={ids_pedido[0]}. Se omite la creación."
                    )
                    return ids_pedido[0]
            except Exception as error:
                registro.warning(
                    f"Búsqueda de duplicado por notas falló: {error}. "
                    "Intentando búsqueda por proveedor + fecha."
                )
 
        # Estrategia 2: buscar por proveedor + fecha de pedido
        try:
            ids_pedido = self._llamar(
                "purchase.order",
                "search",
                [[
                    ["partner_id", "=", id_proveedor],
                    ["date_order", "like", str(factura.fecha)],
                ]],
                {"limit": 5},
            )
            if ids_pedido:
                # Verificar importe para mayor precisión
                pedidos = self._llamar(
                    "purchase.order",
                    "read",
                    [ids_pedido],
                    {"fields": ["id", "amount_total"]},
                )
                for pedido in pedidos:
                    if abs(pedido["amount_total"] - float(factura.total)) < 0.01:
                        registro.warning(
                            f"Pedido ya existente para factura {factura.numero_factura} "
                            f"(por proveedor+fecha+importe): ID={pedido['id']}. "
                            "Se omite la creación."
                        )
                        return pedido["id"]
        except Exception as error:
            registro.warning(f"Búsqueda de duplicado por fecha falló: {error}.")
 
        return None
 
    def _construir_lineas_pedido(self, factura: Factura) -> list[tuple]:
        """
        Convierte las líneas de la factura en el formato de Odoo
        para order_line: lista de tuplas (0, 0, {valores}).
        """
        lineas = []
        for linea in factura.lineas:
            id_producto = self._resolver_producto_linea(linea)
            datos_linea = {
                "name":        linea.descripcion,
                "product_qty": float(linea.cantidad),
                "price_unit":  float(linea.precio_unitario),
            }
            # product_id es opcional: si no se resolvió, Odoo lo admite sin él
            if id_producto is not None:
                datos_linea["product_id"] = id_producto
 
            lineas.append((0, 0, datos_linea))
        return lineas
 
    def _resolver_producto_linea(self, linea) -> int | None:
        """Resuelve el producto de una línea de factura usando GestorProductos."""
        try:
            resultado = self.productos.resolver_producto(
                DatosProducto(
                    nombre=linea.descripcion,
                    codigo_barras=getattr(linea, "referencia", None),
                    precio=linea.precio_unitario,
                    tipo=TipoProducto.SERVICIO,
                    comprable=True,
                    vendible=False,
                )
            )
            registro.debug(
                f"Producto '{linea.descripcion}' resuelto: "
                f"ID={resultado.id_producto} ({resultado.origen.value})"
            )
            return resultado.id_producto
        except (ErrorValidacionProducto, ErrorApiOdoo) as error:
            registro.warning(
                f"No se pudo resolver producto '{linea.descripcion}': {error}"
            )
            return None
 
    def _construir_notas_pedido(self, factura: Factura) -> str:
        """Genera el texto de notas del pedido con los datos de la factura."""
        return (
            f"Factura: {factura.numero_factura}\n"
            f"Fecha: {factura.fecha}\n"
            f"Total bruto: {factura.total_bruto or factura.total}€\n"
            f"Base imponible: {factura.suma_bases_imponibles}€\n"
            f"IVA total: {factura.suma_cuotas_iva}€\n"
            f"Total: {factura.total}€\n"
            f"Forma de pago: {factura.forma_pago or 'No especificada'}\n"
            f"Vencimiento: {factura.vencimiento or 'No especificado'}\n"
            f"Importado automáticamente por automatizacion_facturas"
        )
 
    def crear_pedido_compra(self, factura: Factura) -> int:
        """
        Crea un pedido de compra (purchase.order) en Odoo a partir
        de una factura validada.
 
        Flujo:
          1. Buscar o crear el proveedor
          2. Verificar que no existe ya un pedido para esta factura
          3. Construir y crear el pedido con sus líneas
 
        Returns:
            ID del purchase.order creado (o existente si era duplicado).
        """
        id_proveedor = self.obtener_o_crear_proveedor(factura)
 
        id_existente = self._buscar_pedido_existente(factura, id_proveedor)
        if id_existente:
            return id_existente
 
        lineas_pedido = self._construir_lineas_pedido(factura)
 
        # Si no hay líneas de detalle, crear una línea resumen
        if not lineas_pedido:
            lineas_pedido = [(0, 0, {
                "name":        f"Factura {factura.numero_factura}",
                "product_qty": 1.0,
                "price_unit":  float(factura.total),
            })]
 
        datos_pedido = {
            "partner_id": id_proveedor,
            "date_order":  str(factura.fecha),
            "order_line":  lineas_pedido,
        }
 
        # Añadir notas solo si el campo existe en este Odoo
        if self._CAMPO_NOTAS_PEDIDO:
            datos_pedido[self._CAMPO_NOTAS_PEDIDO] = self._construir_notas_pedido(factura)
 
        # date_planned es opcional
        if factura.vencimiento:
            datos_pedido["date_planned"] = str(factura.vencimiento)
 
        id_pedido = self._llamar("purchase.order", "create", [datos_pedido])
 
        registro.success(
            f"Pedido creado en Odoo: ID={id_pedido} | "
            f"Proveedor: {factura.proveedor} | "
            f"Factura: {factura.numero_factura} | "
            f"Total: {factura.total}€"
        )
        return id_pedido
 