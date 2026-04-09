"""
Tests unitarios del GestorProductos y del ValidadorDatosProducto.

Todas las llamadas XML-RPC se sustituyen por mocks que simulan
el comportamiento real de la API de Odoo.

Ejecutar: pytest pruebas/ -v
"""

import sys
import xmlrpc.client
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.odoo.gestor_productos import (
    DatosProducto,
    ErrorApiOdoo,
    ErrorValidacionProducto,
    GestorProductos,
    OrigenResolucion,
    ResultadoProducto,
    TipoProducto,
    ValidadorDatosProducto,
)


# Helpers de test

def _registro_odoo(
    id_: int = 1,
    nombre: str = "MIMOSA",
    codigo_barras: str | None = None,
    precio: float = 3.25,
) -> dict:
    """Simula un registro de product.product devuelto por Odoo."""
    return {
        "id":         id_,
        "name":       nombre,
        "barcode":    codigo_barras or False,
        "list_price": precio,
        "type":       "service",
        "active":     True,
    }


def _mock_llamada(
    ids_por_nombre:  list[int] | None = None,
    ids_por_codigo:  list[int] | None = None,
    registro_leido:  dict | None = None,
    id_creado:       int = 99,
):
    """
    Construye un mock de `_llamar` que responde según el tipo de operación.
    Detecta el tipo de búsqueda por el primer campo del dominio.
    """
    def _llamar(modelo, metodo, args, opciones=None):
        if modelo != "product.product":
            return []
        if metodo == "search":
            dominio  = args[0]
            campo    = dominio[0][0] if dominio else ""
            if campo == "name":
                return ids_por_nombre or []
            if campo == "barcode":
                return ids_por_codigo or []
            return []
        if metodo == "read":
            return [registro_leido] if registro_leido else []
        if metodo == "create":
            return id_creado
        return []

    return MagicMock(side_effect=_llamar)


# Validador Datos Producto

class TestValidadorDatosProducto:

    def setup_method(self):
        self.validador = ValidadorDatosProducto()

    # Nombre
    def test_nombre_obligatorio(self):
        errores = self.validador.validar(DatosProducto(nombre="  "))
        assert any("obligatorio" in e.lower() for e in errores)

    def test_nombre_demasiado_largo(self):
        errores = self.validador.validar(DatosProducto(nombre="A" * 251))
        assert any("250" in e for e in errores)

    def test_nombre_en_limite_maximo_es_valido(self):
        assert self.validador.validar(DatosProducto(nombre="A" * 250)) == []

    # Precio
    def test_precio_negativo_es_rechazado(self):
        errores = self.validador.validar(DatosProducto(nombre="X", precio=-0.01))
        assert any("negativo" in e.lower() for e in errores)

    def test_precio_cero_es_valido(self):
        assert self.validador.validar(DatosProducto(nombre="X", precio=0)) == []

    def test_precio_nulo_es_valido(self):
        assert self.validador.validar(DatosProducto(nombre="X", precio=None)) == []

    def test_precio_no_numerico_es_rechazado(self):
        producto = DatosProducto(nombre="X")
        object.__setattr__(producto, "precio", "gratis")
        errores = self.validador.validar(producto)
        assert any("número" in e.lower() for e in errores)

    # Código de barras
    def test_codigo_barras_vacio_es_rechazado(self):
        producto = DatosProducto(nombre="X")
        object.__setattr__(producto, "codigo_barras", "")
        errores = self.validador.validar(producto)
        assert any("vacío" in e.lower() or "barras" in e.lower() for e in errores)

    def test_codigo_barras_demasiado_largo(self):
        errores = self.validador.validar(DatosProducto(nombre="X", codigo_barras="1" * 101))
        assert any("100" in e for e in errores)

    def test_codigo_barras_alfanumerico_es_valido(self):
        assert self.validador.validar(
            DatosProducto(nombre="X", codigo_barras="8410169022588")
        ) == []

    def test_codigo_barras_con_guiones_es_valido(self):
        assert self.validador.validar(
            DatosProducto(nombre="X", codigo_barras="841-016-9022")
        ) == []

    def test_codigo_barras_con_caracteres_invalidos(self):
        errores = self.validador.validar(DatosProducto(nombre="X", codigo_barras="841@022#"))
        assert any("no válidos" in e for e in errores)

    # validar_o_lanzar
    def test_lanza_excepcion_con_datos_invalidos(self):
        with pytest.raises(ErrorValidacionProducto) as info:
            self.validador.validar_o_lanzar(DatosProducto(nombre="", precio=-1))
        assert "inválidos" in str(info.value)

    def test_no_lanza_excepcion_con_datos_validos(self):
        self.validador.validar_o_lanzar(DatosProducto(nombre="MIMOSA", precio=3.25))


# Gestor Productos - resolución

class TestResolucionPorNombre:

    def test_encontrado_por_nombre_devuelve_origen_correcto(self):
        producto = _registro_odoo(id_=10, nombre="MIMOSA")
        gestor   = GestorProductos(_mock_llamada(ids_por_nombre=[10], registro_leido=producto))
        resultado = gestor.resolver_producto(DatosProducto(nombre="MIMOSA"))

        assert resultado.id_producto == 10
        assert resultado.origen == OrigenResolucion.ENCONTRADO_POR_NOMBRE
        assert resultado.fue_encontrado is True
        assert resultado.fue_creado is False

    def test_encontrado_por_nombre_no_busca_por_codigo(self):
        producto = _registro_odoo(id_=10, nombre="MIMOSA")
        mock     = _mock_llamada(ids_por_nombre=[10], registro_leido=producto)
        GestorProductos(mock).resolver_producto(
            DatosProducto(nombre="MIMOSA", codigo_barras="123456")
        )
        busquedas_codigo = [
            c for c in mock.call_args_list
            if c.args[1] == "search" and c.args[2][0][0][0] == "barcode"
        ]
        assert len(busquedas_codigo) == 0


class TestResolucionPorCodigo:

    def test_encontrado_por_codigo_cuando_nombre_no_existe(self):
        producto = _registro_odoo(id_=20, nombre="EUCALIPTO", codigo_barras="8410169022588")
        gestor   = GestorProductos(
            _mock_llamada(ids_por_nombre=[], ids_por_codigo=[20], registro_leido=producto)
        )
        resultado = gestor.resolver_producto(
            DatosProducto(nombre="EUCALIPTO CINEREA", codigo_barras="8410169022588")
        )
        assert resultado.id_producto == 20
        assert resultado.origen == OrigenResolucion.ENCONTRADO_POR_CODIGO
        assert resultado.fue_encontrado is True

    def test_encontrado_por_codigo_no_crea_nuevo_producto(self):
        producto = _registro_odoo(id_=20, codigo_barras="8410169022588")
        mock     = _mock_llamada(ids_por_nombre=[], ids_por_codigo=[20], registro_leido=producto)
        GestorProductos(mock).resolver_producto(
            DatosProducto(nombre="NOMBRE DIFERENTE", codigo_barras="8410169022588")
        )
        llamadas_create = [c for c in mock.call_args_list if c.args[1] == "create"]
        assert len(llamadas_create) == 0


class TestCreacionProducto:

    def test_creado_cuando_no_existe_por_ningun_criterio(self):
        nuevo   = _registro_odoo(id_=99, nombre="NUEVO PRODUCTO")
        gestor  = GestorProductos(
            _mock_llamada(ids_por_nombre=[], ids_por_codigo=[], registro_leido=nuevo, id_creado=99)
        )
        resultado = gestor.resolver_producto(
            DatosProducto(nombre="NUEVO PRODUCTO", codigo_barras="1234567890123")
        )
        assert resultado.id_producto == 99
        assert resultado.origen == OrigenResolucion.CREADO
        assert resultado.fue_creado is True

    def test_sin_codigo_barras_omite_busqueda_por_codigo(self):
        nuevo = _registro_odoo(id_=99, nombre="SIN CODIGO")
        mock  = _mock_llamada(ids_por_nombre=[], registro_leido=nuevo, id_creado=99)
        GestorProductos(mock).resolver_producto(DatosProducto(nombre="SIN CODIGO"))
        busquedas_barcode = [
            c for c in mock.call_args_list
            if c.args[1] == "search" and c.args[2][0][0][0] == "barcode"
        ]
        assert len(busquedas_barcode) == 0

    def test_valores_enviados_a_odoo_son_correctos(self):
        valores_capturados = {}

        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                return []
            if metodo == "create":
                valores_capturados.update(args[0])
                return 55
            if metodo == "read":
                return [_registro_odoo(id_=55, nombre="COMPLETO")]
            return []

        GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
            DatosProducto(
                nombre="COMPLETO",
                codigo_barras="841016902",
                precio=12.50,
                tipo=TipoProducto.CONSUMIBLE,
                comprable=True,
                vendible=True,
                referencia_interna="REF-001",
                descripcion="Descripción de prueba",
            )
        )
        assert valores_capturados["name"]            == "COMPLETO"
        assert valores_capturados["barcode"]         == "841016902"
        assert valores_capturados["list_price"]      == 12.50
        assert valores_capturados["type"]            == "consu"
        assert valores_capturados["purchase_ok"]     is True
        assert valores_capturados["sale_ok"]         is True
        assert valores_capturados["default_code"]    == "REF-001"

    def test_campos_nulos_no_se_envian_a_odoo(self):
        capturado = {}

        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                return []
            if metodo == "create":
                capturado.update(args[0])
                return 1
            if metodo == "read":
                return [_registro_odoo(id_=1)]
            return []

        GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
            DatosProducto(nombre="SIMPLE")
        )
        for campo_opcional in ["list_price", "barcode", "description", "default_code"]:
            assert campo_opcional not in capturado


# Gestor Productos - manejo de errores

class TestErroresGestorProductos:

    def test_nombre_vacio_lanza_error_validacion(self):
        with pytest.raises(ErrorValidacionProducto):
            GestorProductos(MagicMock()).resolver_producto(DatosProducto(nombre=""))

    def test_precio_negativo_lanza_error_validacion(self):
        with pytest.raises(ErrorValidacionProducto):
            GestorProductos(MagicMock()).resolver_producto(
                DatosProducto(nombre="X", precio=-5.0)
            )

    def test_fallo_api_en_busqueda_lanza_error_odoo(self):
        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                raise xmlrpc.client.Fault(1, "Access Denied")
            return []

        with pytest.raises(ErrorApiOdoo, match="Access Denied"):
            GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
                DatosProducto(nombre="MIMOSA")
            )

    def test_fallo_api_en_creacion_lanza_error_odoo(self):
        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                return []
            if metodo == "create":
                raise xmlrpc.client.Fault(1, "Constraint violated")
            return []

        with pytest.raises(ErrorApiOdoo, match="Constraint violated"):
            GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
                DatosProducto(nombre="NUEVO")
            )

    def test_race_condition_barcode_duplicado(self):
        """
        Si create falla por barcode duplicado (race condition), el gestor debe
        recuperar el producto existente en lugar de propagar el error.
        """
        existente   = _registro_odoo(id_=77, codigo_barras="RACE123")
        cuenta      = {"busquedas_codigo": 0}

        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                campo = args[0][0][0] if args[0] else ""
                if campo == "name":
                    return []
                if campo == "barcode":
                    cuenta["busquedas_codigo"] += 1
                    # Primera llamada: aún no existe. Segunda: ya existe.
                    return [] if cuenta["busquedas_codigo"] == 1 else [77]
            if metodo == "create":
                raise xmlrpc.client.Fault(1, "unique constraint barcode violated")
            if metodo == "read":
                return [existente]
            return []

        resultado = GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
            DatosProducto(nombre="PRODUCTO RACE", codigo_barras="RACE123")
        )
        assert resultado.id_producto == 77
        assert resultado.origen == OrigenResolucion.ENCONTRADO_POR_CODIGO

    def test_id_invalido_devuelto_por_odoo_lanza_error(self):
        def llamada(modelo, metodo, args, opciones=None):
            return [] if metodo == "search" else 0  # ID inválido

        with pytest.raises(ErrorApiOdoo, match="ID inválido"):
            GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
                DatosProducto(nombre="BAD RETURN")
            )

    def test_lectura_vacia_tras_creacion_lanza_error(self):
        def llamada(modelo, metodo, args, opciones=None):
            if metodo == "search":
                return []
            if metodo == "create":
                return 42
            return []  # read vacío

        with pytest.raises(ErrorApiOdoo, match="no encontrado"):
            GestorProductos(MagicMock(side_effect=llamada)).resolver_producto(
                DatosProducto(nombre="FANTASMA")
            )


# Resultado Producto

class TestResultadoProducto:

    def test_fue_creado_solo_para_creados(self):
        r = ResultadoProducto(1, OrigenResolucion.CREADO)
        assert r.fue_creado is True
        assert r.fue_encontrado is False

    def test_fue_encontrado_para_nombre(self):
        r = ResultadoProducto(1, OrigenResolucion.ENCONTRADO_POR_NOMBRE)
        assert r.fue_encontrado is True
        assert r.fue_creado is False

    def test_fue_encontrado_para_codigo(self):
        r = ResultadoProducto(1, OrigenResolucion.ENCONTRADO_POR_CODIGO)
        assert r.fue_encontrado is True
        assert r.fue_creado is False


# Datos Producto — normalización 

class TestNormalizacionDatosProducto:

    def test_nombre_se_elimina_espacios(self):
        assert DatosProducto(nombre="  MIMOSA  ").nombre == "MIMOSA"

    def test_codigo_barras_se_elimina_espacios(self):
        assert DatosProducto(nombre="X", codigo_barras="  1234  ").codigo_barras == "1234"

    def test_referencia_interna_se_elimina_espacios(self):
        assert DatosProducto(nombre="X", referencia_interna="  REF-001  ").referencia_interna == "REF-001"

    def test_codigo_barras_nulo_se_conserva(self):
        assert DatosProducto(nombre="X", codigo_barras=None).codigo_barras is None


# Tests de integración (flujos completos) 

class TestFlujosCompletos:

    def test_idempotencia_por_nombre(self):
        """Llamar dos veces con el mismo nombre devuelve siempre el mismo ID."""
        producto = _registro_odoo(id_=5, nombre="LAVANDA")
        mock     = _mock_llamada(ids_por_nombre=[5], registro_leido=producto)
        gestor   = GestorProductos(mock)

        r1 = gestor.resolver_producto(DatosProducto(nombre="LAVANDA"))
        r2 = gestor.resolver_producto(DatosProducto(nombre="LAVANDA"))

        assert r1.id_producto == r2.id_producto == 5
        assert r1.origen == r2.origen == OrigenResolucion.ENCONTRADO_POR_NOMBRE

    def test_por_codigo_usa_nombre_de_odoo(self):
        """Al encontrar por código, el nombre que prevalece es el de Odoo."""
        existente = _registro_odoo(id_=30, nombre="NOMBRE EN ODOO", codigo_barras="BC-001")
        mock      = _mock_llamada(ids_por_nombre=[], ids_por_codigo=[30], registro_leido=existente)
        resultado = GestorProductos(mock).resolver_producto(
            DatosProducto(nombre="NOMBRE DIFERENTE", codigo_barras="BC-001")
        )
        assert resultado.datos_odoo["name"] == "NOMBRE EN ODOO"
        assert resultado.origen == OrigenResolucion.ENCONTRADO_POR_CODIGO
