"""
Configuración del sistema de registro (logging) con loguru.

Centraliza toda la configuración en un único punto. Las demás partes
del proyecto solo importan `registro` desde este módulo.

Salidas configuradas:
  - Consola: formato coloreado para desarrollo
  - registros/aplicacion.log: todos los niveles, rotación cada 10 MB
  - registros/errores.log: solo errores, con trazas completas
"""

import sys
from loguru import logger

from configuracion.ajustes import ajustes


def _configurar_registro() -> None:
    """Inicializa los manejadores de loguru."""
    directorio = ajustes.obtener_directorio_registros()
    nivel = ajustes.nivel_registro.upper()

    logger.remove()

    # Salida a consola con colores
    logger.add(
        sys.stdout,
        level=nivel,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # Archivo general con rotación cada 10 MB
    logger.add(
        directorio / "aplicacion.log",
        level=nivel,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )

    # Archivo exclusivo de errores con trazas completas
    logger.add(
        directorio / "errores.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}\n{exception}",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    logger.info(f"Registro configurado. Nivel: {nivel}. Directorio: {directorio}")


_configurar_registro()

# Re-exportar para uso en el resto del proyecto
registro = logger
