"""
Tests unitarios de la BaseAuditoria SQLite.

Usa bases de datos temporales en memoria para cada test,
garantizando aislamiento total.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.base_auditoria import BaseAuditoria, EstadoFactura


@pytest.fixture
def auditoria(tmp_path):
    """Instancia de BaseAuditoria con BD temporal por test."""
    return BaseAuditoria(str(tmp_path / "test_auditoria.db"))


class TestRegistroCorrecto:

    def test_correo_nuevo_devuelve_verdadero(self, auditoria):
        assert auditoria.registrar_correo("correo_001") is True

    def test_correo_duplicado_devuelve_falso(self, auditoria):
        auditoria.registrar_correo("correo_001")
        assert auditoria.registrar_correo("correo_001") is False

    def test_distintos_correos_se_registran_independientemente(self, auditoria):
        assert auditoria.registrar_correo("correo_001") is True
        assert auditoria.registrar_correo("correo_002") is True


class TestDeteccionDuplicados:

    def test_pdf_no_procesado_no_es_duplicado(self, auditoria):
        contenido = b"contenido_pdf_de_prueba"
        assert auditoria.es_pdf_duplicado(contenido) is False

    def test_pdf_procesado_es_detectado_como_duplicado(self, auditoria):
        import hashlib
        contenido  = b"contenido_factura_real"
        hash_pdf   = hashlib.sha256(contenido).hexdigest()

        auditoria.registrar_correo("correo_dup")
        auditoria.actualizar_estado(
            "correo_dup", EstadoFactura.PROCESADA, hash_pdf=hash_pdf
        )
        assert auditoria.es_pdf_duplicado(contenido) is True

    def test_pdf_fallido_no_es_considerado_duplicado(self, auditoria):
        import hashlib
        contenido = b"pdf_con_fallo"
        hash_pdf  = hashlib.sha256(contenido).hexdigest()

        auditoria.registrar_correo("correo_fallo")
        auditoria.actualizar_estado(
            "correo_fallo", EstadoFactura.FALLIDA, hash_pdf=hash_pdf
        )
        assert auditoria.es_pdf_duplicado(contenido) is False


class TestActualizacionEstado:

    def test_estado_se_actualiza_correctamente(self, auditoria):
        auditoria.registrar_correo("correo_upd")
        auditoria.actualizar_estado("correo_upd", EstadoFactura.PROCESADA)
        stats = auditoria.obtener_estadisticas()
        assert stats.get("procesada", 0) >= 1

    def test_intentos_se_incrementan(self, auditoria):
        auditoria.registrar_correo("correo_intentos")
        auditoria.actualizar_estado("correo_intentos", EstadoFactura.PROCESANDO)
        auditoria.actualizar_estado("correo_intentos", EstadoFactura.FALLIDA)
        fallidas = auditoria.obtener_facturas_fallidas(max_intentos=10)
        registro = next(r for r in fallidas if r["id_correo"] == "correo_intentos")
        assert registro["intentos"] >= 2


class TestFacturasFallidas:

    def test_recupera_facturas_con_menos_intentos_del_maximo(self, auditoria):
        auditoria.registrar_correo("fallo_001")
        auditoria.actualizar_estado(
            "fallo_001", EstadoFactura.FALLIDA,
            mensaje_error="Error de prueba"
        )
        fallidas = auditoria.obtener_facturas_fallidas(max_intentos=5)
        assert any(r["id_correo"] == "fallo_001" for r in fallidas)

    def test_no_devuelve_facturas_agotadas(self, auditoria):
        auditoria.registrar_correo("agotada_001")
        # Simular 3 intentos actualizando tres veces
        for _ in range(3):
            auditoria.actualizar_estado("agotada_001", EstadoFactura.FALLIDA)
        fallidas = auditoria.obtener_facturas_fallidas(max_intentos=3)
        assert not any(r["id_correo"] == "agotada_001" for r in fallidas)

    def test_devuelve_lista_vacia_si_no_hay_fallidas(self, auditoria):
        assert auditoria.obtener_facturas_fallidas() == []


class TestEstadisticas:

    def test_estadisticas_incluyen_todos_los_estados(self, auditoria):
        auditoria.registrar_correo("e1")
        auditoria.registrar_correo("e2")
        auditoria.actualizar_estado("e2", EstadoFactura.PROCESADA)

        stats = auditoria.obtener_estadisticas()
        assert isinstance(stats, dict)
        assert sum(stats.values()) >= 2

    def test_estadisticas_bd_vacia_devuelve_diccionario_vacio(self, auditoria):
        assert auditoria.obtener_estadisticas() == {}
