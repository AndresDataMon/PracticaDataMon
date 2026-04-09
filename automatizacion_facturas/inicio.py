"""
Punto de entrada de la aplicación automatizacion_facturas.

Modos de uso:
  python inicio.py                          → Monitor Gmail continuo
  python inicio.py --archivo factura.pdf    → Procesar un PDF local
  python inicio.py --archivo f.pdf --sin-odoo → Solo extracción y validación
  python inicio.py --estadisticas           → Ver estadísticas de procesamiento
  python inicio.py --fallidas               → Listar facturas fallidas
  python inicio.py --verificar-config       → Comprobar configuración y conexiones
"""

import sys
import argparse
import xmlrpc.client
from pathlib import Path

# Asegurar que el directorio raíz esté en el path de importaciones
sys.path.insert(0, str(Path(__file__).parent))

from utils.registro import registro
from config.ajustes import ajustes


# Comandos 

def _cmd_monitor(argumentos) -> None:
    """Modo producción: monitoriza Gmail de forma continua."""
    from core.orquestador import OrquestadorFacturas
    orquestador = OrquestadorFacturas()
    registro.info("Iniciando monitor de Gmail...")
    orquestador.ejecutar()


def _cmd_procesar_archivo(argumentos) -> None:
    """Procesa un único PDF local y muestra el resultado."""
    from core.orquestador import OrquestadorFacturas

    ruta_pdf = Path(argumentos.archivo)
    if not ruta_pdf.exists():
        registro.error(f"Archivo no encontrado: {ruta_pdf}")
        sys.exit(1)

    orquestador = OrquestadorFacturas(sin_odoo=argumentos.sin_odoo)
    resultado   = orquestador.procesar_archivo_pdf(ruta_pdf)

    if resultado["exitoso"]:
        registro.success("✓ Factura procesada correctamente")
        if resultado["id_pedido"]:
            registro.success(f"  → Pedido Odoo creado: ID {resultado['id_pedido']}")
        if resultado["datos_factura"]:
            datos = resultado["datos_factura"]
            registro.info(f"  → Proveedor : {datos.get('proveedor')}")
            registro.info(f"  → Factura   : {datos.get('numero_factura')}")
            registro.info(f"  → Fecha     : {datos.get('fecha')}")
            registro.info(f"  → Total     : {datos.get('total')}€")
    else:
        registro.error("✗ Procesamiento fallido:")
        for error in resultado["errores"]:
            registro.error(f"  - {error}")
        sys.exit(1)


def _cmd_estadisticas(argumentos) -> None:
    """Muestra el recuento de facturas agrupado por estado."""
    from core.orquestador import OrquestadorFacturas

    orquestador  = OrquestadorFacturas(sin_odoo=True)
    estadisticas = orquestador.obtener_estadisticas()
    total        = sum(estadisticas.values())

    registro.info("=== Estadísticas de procesamiento ===")
    for estado, cantidad in sorted(estadisticas.items()):
        porcentaje = (cantidad / total * 100) if total > 0 else 0
        registro.info(f"  {estado:<12}: {cantidad:>4}  ({porcentaje:.1f}%)")
    registro.info(f"  {'TOTAL':<12}: {total:>4}")


def _cmd_facturas_fallidas(argumentos) -> None:
    """Lista las facturas fallidas que aún pueden reintentarse."""
    from utils.base_auditoria import BaseAuditoria

    auditoria = BaseAuditoria()
    fallidas  = auditoria.obtener_facturas_fallidas(max_intentos=ajustes.max_reintentos)

    if not fallidas:
        registro.info("No hay facturas fallidas pendientes de reintento.")
        return

    registro.info(f"Facturas fallidas pendientes ({len(fallidas)}):")
    for registro_bd in fallidas:
        registro.warning(
            f"  - id_correo: {registro_bd['id_correo']} | "
            f"factura: {registro_bd.get('numero_factura', 'desconocida')} | "
            f"intentos: {registro_bd['intentos']} | "
            f"error: {str(registro_bd.get('mensaje_error', ''))[:80]}"
        )
    registro.info(
        "Para reintentar, ejecuta: python inicio.py --archivo <ruta_pdf>"
    )


def _cmd_verificar_config(argumentos) -> None:
    """Comprueba que la configuración y las conexiones son correctas."""
    errores    = []
    advertencias = []

    registro.info("=== Verificando configuración ===\n")

    _verificar_archivo_env(advertencias)
    _verificar_credenciales_gmail(advertencias)
    _verificar_clave_gemini(errores)
    _mostrar_config_odoo()

    if not argumentos.omitir_test_odoo:
        _verificar_conexion_odoo(advertencias)

    _mostrar_resumen_verificacion(errores, advertencias)


def _verificar_archivo_env(advertencias: list) -> None:
    if Path(".env").exists():
        registro.success("  ✓ Archivo .env encontrado")
    else:
        advertencias.append(".env no encontrado (se usarán valores por defecto)")
        registro.warning("  ⚠ .env no encontrado")


def _verificar_credenciales_gmail(advertencias: list) -> None:
    ruta = Path(ajustes.ruta_credenciales_gmail)
    if ruta.exists():
        registro.success(f"  ✓ credenciales.json encontrado: {ruta}")
    else:
        advertencias.append(f"credenciales.json no encontrado en {ruta}")
        registro.warning(f"  ⚠ credenciales.json no encontrado: {ruta}")


def _verificar_clave_gemini(errores: list) -> None:
    if ajustes.clave_api_gemini and len(ajustes.clave_api_gemini) > 10:
        registro.success(
            f"  ✓ CLAVE_API_GEMINI configurada (modelo: {ajustes.modelo_gemini})"
        )
    else:
        errores.append("CLAVE_API_GEMINI no configurada o inválida")
        registro.error("  ✗ CLAVE_API_GEMINI no configurada")


def _mostrar_config_odoo() -> None:
    registro.info(f"  ℹ URL Odoo   : {ajustes.url_odoo}")
    registro.info(f"  ℹ BD Odoo    : {ajustes.base_datos_odoo}")
    registro.info(f"  ℹ Usuario    : {ajustes.usuario_odoo}")


def _verificar_conexion_odoo(advertencias: list) -> None:
    try:
        servidor = xmlrpc.client.ServerProxy(f"{ajustes.url_odoo}/xmlrpc/2/common")
        version  = servidor.version()
        registro.success(
            f"  ✓ Conexión Odoo exitosa: v{version.get('server_version', '?')}"
        )
    except Exception as error:
        advertencias.append(f"No se pudo conectar a Odoo: {error}")
        registro.warning(f"  ⚠ Conexión Odoo fallida: {error}")


def _mostrar_resumen_verificacion(errores: list, advertencias: list) -> None:
    registro.info("")
    if errores:
        registro.error(f"✗ {len(errores)} error(es) crítico(s):")
        for error in errores:
            registro.error(f"  - {error}")
        sys.exit(1)
    elif advertencias:
        registro.warning(f"⚠ Configuración OK con {len(advertencias)} advertencia(s)")
    else:
        registro.success("✓ Configuración correcta. Listo para ejecutar.")


# CLI 

def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="automatizacion_facturas — Pipeline: Gmail → Gemini → Odoo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python inicio.py                                  # Monitor Gmail continuo
  python inicio.py --archivo factura.pdf            # Procesar PDF local
  python inicio.py --archivo factura.pdf --sin-odoo # Solo extracción + validación
  python inicio.py --estadisticas                   # Ver estadísticas
  python inicio.py --verificar-config               # Verificar configuración
        """,
    )
    parser.add_argument(
        "--archivo", "-a",
        metavar="RUTA_PDF",
        help="Ruta a un PDF local para procesar directamente",
    )
    parser.add_argument(
        "--sin-odoo",
        action="store_true",
        help="Ejecutar sin conexión a Odoo (solo extracción y validación)",
    )
    parser.add_argument(
        "--estadisticas",
        action="store_true",
        help="Mostrar estadísticas de procesamiento y salir",
    )
    parser.add_argument(
        "--fallidas",
        action="store_true",
        help="Listar facturas fallidas pendientes de reintento",
    )
    parser.add_argument(
        "--verificar-config",
        action="store_true",
        help="Verificar la configuración y las conexiones externas",
    )
    parser.add_argument(
        "--omitir-test-odoo",
        action="store_true",
        help="Omitir el test de conexión a Odoo en --verificar-config",
    )
    return parser


def main() -> None:
    parser     = _construir_parser()
    argumentos = parser.parse_args()

    registro.info("=" * 58)
    registro.info("  automatizacion_facturas  |  Gmail → Gemini → Odoo")
    registro.info("=" * 58)

    if argumentos.verificar_config:
        _cmd_verificar_config(argumentos)
    elif argumentos.estadisticas:
        _cmd_estadisticas(argumentos)
    elif argumentos.fallidas:
        _cmd_facturas_fallidas(argumentos)
    elif argumentos.archivo:
        _cmd_procesar_archivo(argumentos)
    else:
        _cmd_monitor(argumentos)


if __name__ == "__main__":
    main()
