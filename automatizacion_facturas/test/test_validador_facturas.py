"""
Tests unitarios del esquema Factura y del ValidadorFacturas.

No requieren conexión a servicios externos.
Ejecutar: pytest pruebas/ -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.validator.esquema_factura import Factura, LineaFactura
from core.validator.validador_facturas import ValidadorFacturas


# Datos de prueba

@pytest.fixture
def datos_factura_valida():
    """Factura real basada en el ejemplo de Vivers Torrents."""
    return {
        "proveedor":           "VIVERS TORRENTS, SL",
        "cif":                 "B64056419",
        "direccion_proveedor": "CRA. SANT MARTÍ SARROCA, KM. 2",
        "numero_factura":      "000778/2026",
        "fecha":               "2026-02-12",
        "base_imponible_10":   34.45,
        "base_imponible_21":   50.45,
        "iva_10":              3.44,
        "iva_21":              10.59,
        "total_bruto":         84.90,
        "total":               98.93,
        "forma_pago":          "GIR BANCARI QUINZENAL",
        "vencimiento":         "2026-02-13",
        "lineas": [
            {
                "descripcion":     "MIMOSA",
                "referencia":      "147.487",
                "cantidad":        5,
                "precio_unitario": 3.25,
                "iva_pct":         10,
                "subtotal":        16.25,
            },
            {
                "descripcion":     "EUCALIPTO CINEREA (PLATA)",
                "cantidad":        5,
                "precio_unitario": 4.50,
                "iva_pct":         21,
                "subtotal":        22.50,
            },
        ],
    }


# Validación exitosa 

class TestFacturaValida:

    def test_factura_completa_es_valida(self, datos_factura_valida):
        v = ValidadorFacturas()
        resultado = v.validar(datos_factura_valida)
        assert resultado.es_valido is True
        assert resultado.factura is not None
        assert resultado.errores == []

    def test_campos_parseados_correctamente(self, datos_factura_valida):
        factura = Factura.model_validate(datos_factura_valida)
        assert factura.proveedor == "VIVERS TORRENTS, SL"
        assert factura.cif       == "B64056419"
        assert factura.total     == 98.93
        assert len(factura.lineas) == 2

    def test_propiedades_calculadas(self, datos_factura_valida):
        factura = Factura.model_validate(datos_factura_valida)
        assert factura.suma_bases_imponibles == pytest.approx(84.90, abs=0.01)
        assert factura.suma_cuotas_iva       == pytest.approx(14.03, abs=0.01)

    def test_acepta_multiples_formatos_de_fecha(self, datos_factura_valida):
        validador = ValidadorFacturas()
        for formato in ["2026-02-12", "12/02/2026", "12-02-2026"]:
            datos = {**datos_factura_valida, "fecha": formato}
            assert validador.validar(datos).es_valido, \
                f"El formato de fecha '{formato}' debería ser válido"

    def test_tolerancia_en_coherencia_de_importes(self, datos_factura_valida):
        """Una diferencia de 6 céntimos en el total es aceptable."""
        datos = {**datos_factura_valida, "total": 98.99}
        assert ValidadorFacturas().validar(datos).es_valido

    def test_factura_sin_lineas_de_detalle_es_valida(self, datos_factura_valida):
        datos = {**datos_factura_valida, "lineas": []}
        assert ValidadorFacturas().validar(datos).es_valido


# Validación de CIF/NIF 

class TestValidacionCIF:

    def test_cif_empresa_valido(self, datos_factura_valida):
        datos = {**datos_factura_valida, "cif": "B64056419"}
        assert ValidadorFacturas().validar(datos).es_valido

    def test_nif_persona_fisica_valido(self, datos_factura_valida):
        datos = {**datos_factura_valida, "cif": "12345678Z"}
        assert ValidadorFacturas().validar(datos).es_valido

    def test_nie_extranjero_valido(self, datos_factura_valida):
        datos = {**datos_factura_valida, "cif": "X1234567L"}
        assert ValidadorFacturas().validar(datos).es_valido

    def test_cif_con_formato_incorrecto_es_rechazado(self, datos_factura_valida):
        datos = {**datos_factura_valida, "cif": "12345678"}
        resultado = ValidadorFacturas().validar(datos)
        assert resultado.es_valido is False
        assert any("CIF" in e or "NIF" in e for e in resultado.errores)

    def test_cif_vacio_es_rechazado(self, datos_factura_valida):
        datos = {**datos_factura_valida, "cif": ""}
        assert ValidadorFacturas().validar(datos).es_valido is False


# Campos obligatorios 

class TestCamposObligatorios:

    @pytest.mark.parametrize("campo_a_eliminar", [
        "proveedor", "cif", "numero_factura", "fecha", "total"
    ])
    def test_campo_obligatorio_faltante_invalida_factura(
        self, datos_factura_valida, campo_a_eliminar
    ):
        datos = {k: v for k, v in datos_factura_valida.items() if k != campo_a_eliminar}
        resultado = ValidadorFacturas().validar(datos)
        assert resultado.es_valido is False

    def test_sin_base_imponible_invalida_factura(self, datos_factura_valida):
        datos = {
            **datos_factura_valida,
            "base_imponible_4":  None,
            "base_imponible_10": None,
            "base_imponible_21": None,
        }
        assert ValidadorFacturas().validar(datos).es_valido is False

    def test_entrada_no_diccionario_es_rechazada(self):
        resultado = ValidadorFacturas().validar("esto no es un diccionario")
        assert resultado.es_valido is False
        assert len(resultado.errores) > 0

    def test_diccionario_vacio_es_rechazado(self):
        resultado = ValidadorFacturas().validar({})
        assert resultado.es_valido is False


# Coherencia de importes

class TestCoherenciaImportes:

    def test_total_muy_diferente_es_rechazado(self, datos_factura_valida):
        datos = {**datos_factura_valida, "total": 150.00}
        assert ValidadorFacturas().validar(datos).es_valido is False

    def test_total_negativo_es_rechazado(self, datos_factura_valida):
        datos = {**datos_factura_valida, "total": -10.0}
        assert ValidadorFacturas().validar(datos).es_valido is False


# Líneas de detalle 

class TestLineasFactura:

    def test_subtotal_calculado_automaticamente(self):
        linea = LineaFactura(
            descripcion="MIMOSA",
            cantidad=5,
            precio_unitario=3.25,
            iva_pct=10,
        )
        assert linea.subtotal == pytest.approx(16.25)

    def test_subtotal_informado_se_conserva(self):
        linea = LineaFactura(
            descripcion="PRODUCTO",
            cantidad=2,
            precio_unitario=10.0,
            iva_pct=21,
            subtotal=20.0,
        )
        assert linea.subtotal == 20.0


# Representación del resultado 

class TestResultadoValidacion:

    def test_repr_cuando_es_valido(self, datos_factura_valida):
        resultado = ValidadorFacturas().validar(datos_factura_valida)
        assert "OK" in repr(resultado)
        assert "000778/2026" in repr(resultado)

    def test_repr_cuando_falla(self):
        resultado = ValidadorFacturas().validar({})
        assert "FALLIDO" in repr(resultado)
