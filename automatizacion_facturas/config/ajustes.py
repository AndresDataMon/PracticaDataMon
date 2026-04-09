"""
Gestión centralizada de la configuración de la aplicación.

Lee las variables de entorno desde el archivo .env de forma automática
a través de pydantic-settings. Expone una instancia global `ajustes`
que debe importarse en el resto del proyecto.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Ajustes(BaseSettings):
    """Variables de configuración de toda la aplicación."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Gmail 
    ruta_credenciales_gmail: str  = Field(default="configuracion/credenciales.json")
    ruta_token_gmail: str         = Field(default="configuracion/token.json")
    correo_monitorizado: str      = Field(default="facturas@datamon.es")
    etiqueta_gmail: str           = Field(default="INBOX")
    intervalo_sondeo_segundos: int = Field(default=60, ge=10)

    # Gemini 
    clave_api_gemini: str    = Field(default="")
    modelo_gemini: str       = Field(default="gemini-1.5-pro")
    temperatura_gemini: float = Field(default=0.0, ge=0.0, le=1.0)

    # Odoo
    url_odoo: str      = Field(default="http://localhost:8069")
    base_datos_odoo: str = Field(default="odoo")
    usuario_odoo: str  = Field(default="admin")
    contrasena_odoo: str = Field(default="admin")

    # Aplicación
    nivel_registro: str    = Field(default="INFO")
    directorio_registros: str = Field(default="registros")
    directorio_datos: str  = Field(default="datos")
    max_reintentos: int    = Field(default=3, ge=1)
    pausa_reintento_segundos: int = Field(default=60, ge=5)

    def obtener_directorio_datos(self) -> Path:
        """Devuelve el Path de datos, creándolo si no existe."""
        ruta = Path(self.directorio_datos)
        ruta.mkdir(parents=True, exist_ok=True)
        return ruta

    def obtener_directorio_registros(self) -> Path:
        """Devuelve el Path de registros, creándolo si no existe."""
        ruta = Path(self.directorio_registros)
        ruta.mkdir(parents=True, exist_ok=True)
        return ruta


# Instancia global - importar desde aquí en el resto del proyecto
ajustes = Ajustes()
