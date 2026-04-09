"""
Extractor de datos de facturas usando la API de Gemini (Google GenAI).
 
Envía el PDF como bytes y devuelve un diccionario con los campos extraídos.
En caso de fallo permanente (sin cuota, modelo no disponible) lanza
GeminiErrorPermanente para que el orquestador pueda distinguirlo de
errores transitorios (red, timeout) y no reintente indefinidamente.
"""
 
import json
from pathlib import Path
from typing import Any, Dict, Union
 
from google import genai
from google.genai import types
from google.genai.errors import ClientError
 
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
 
from utils.registro import registro
from config.ajustes import ajustes
 
 
_PROMPT_EXTRACCION = """
Eres un extractor experto de datos de facturas y albaranes comerciales españoles y europeos.
Analiza el documento adjunto (PDF digital o imagen escaneada) y extrae los campos indicados.

REGLAS ESTRICTAS:
1. Devuelve ÚNICAMENTE el JSON, sin texto adicional, sin markdown, sin ```json
2. Si un campo no aparece en el documento, usa null
3. Las fechas siempre en formato ISO: YYYY-MM-DD. Interpreta formatos como "17-02-2026", "17/02/26", "11-02-26", "06/02/26" → "2026-02-17"
4. Los importes como número decimal con punto como separador (1.234,56 € → 1234.56)
5. El CIF/NIF/VAT sin espacios. Puede aparecer como "B64056419", "ESB64056419", "ES-B64056419", "NIF/CIF:", "CIF:", "VAT:" → extrae solo el código limpio
6. Si el documento es un albarán (no factura), igualmente extrae todos los campos disponibles
7. El proveedor es quien emite el documento (emisor), NO el destinatario (que suele ser Vivers Torrents)
8. Para documentos multipágina, los totales e importes fiscales suelen estar en la ÚLTIMA página

IDENTIFICACIÓN DEL DOCUMENTO:
- Puede llamarse: FACTURA, ALBARÀ, ALBARÁN, Factura-Facture-Factuur-Invoice-Rechnung-Fattura
- El número puede aparecer como: "Factura Nº", "Factura nº", "Nº Factura", "Número de factura", "nº", directamente como "000778/2026", "10F26001831", "A/15791", "3.085", "39974", "659"
- La fecha puede aparecer como: "Fecha", "Data", "Date", "Fecha factura", "Data Factura", "Fecha:"

LOCALIZACIÓN DEL CIF/NIF DEL PROVEEDOR:
- Busca junto al nombre del proveedor, en el encabezado o pie de página
- Etiquetas posibles: "CIF:", "NIF:", "C.I.F.", "NIF/CIF:", "NIF/TVA/VAT:", "C.I.F./N.I.F.", "VAT:", "BTW-nr:", "KvK"
- En facturas internacionales puede aparecer como número EORI o VAT europeo (ej: "NL804543306B01") → extrae el número tal cual
- Si aparece con prefijo de país (ESB64056419), extrae solo la parte del NIF: B64056419

BASES IMPONIBLES E IVA:
- IVA al 4%: busca "4%", "4,00", columna "4" — común en productos básicos / semillas
- IVA al 10%: busca "10%", "10,00", columna "10" — común en plantas, alimentos animales, flores
- IVA al 21%: busca "21%", "21,00", columna "21" — común en materiales, accesorios, servicios
- Pueden coexistir múltiples tipos de IVA en la misma factura
- "Import Brut" / "Total Bruto" / "Subtotal" → es el importe antes de descuentos
- "Base Imponible" / "Base Neta" / "Total sin IVA" / "Bienes totales" → es la base sobre la que se aplica IVA
- "Cuota IVA" / "Import IVA" / "IVA" → es el importe del IVA
- "Total Factura" / "Total Fra" / "TOTAL" / "Import a Pagar" → es el total final a pagar
- Recargo de equivalencia (R.E.): si aparece, ignóralo para el cálculo del total (ya está incluido)
- Descuentos: pueden aparecer como "% Dto.", "Descompte", "Desc.", DTO1, DTO2 → aplícalos si afectan a la base imponible
- IRPF: si aparece retención IRPF (ej: "2% IRPF"), réstala del total para obtener el importe real a pagar

FORMA DE PAGO Y VENCIMIENTOS:
- Forma de pago puede llamarse: "Forma de Pago", "Forma de Pagament", "Condición de pago", "Formas de Pag"
- Valores posibles: "GIR BANCARI QUINZENAL", "GIRO 30 DIAS", "Recibo bancario", "Transferencia", "2 Vtos. 30 y 60 días con Recibo Domiciliado", etc.
- Vencimiento puede llamarse: "Vto.", "Vtos:", "Venciments", "Vencimientos", "Echéance", "Fälligkeit"
- Si hay múltiples vencimientos, toma el primero como "vencimiento"
- Si no hay vencimiento explícito pero hay "a 30 dias fecha factura", calcula la fecha sumando 30 días a la fecha de factura

LÍNEAS DE DETALLE:
- Extrae solo líneas de producto/servicio reales; ignora líneas de embalaje, pallets, carros, alzas, lejas, candados, etiquetas, transportes internos, y filas de subtotales intermedios ("SUMA Y SIGUE", "SUMA ANTERIOR")
- El % IVA de cada línea puede aparecer en columna "IVA", "%IVA", "% Iva", o directamente en la descripción
- "precio_unitario": precio neto por unidad (sin IVA), ya descontado si hay descuento
- "subtotal": importe total de la línea (cantidad × precio_unitario, ya con descuento aplicado)
- Si el precio aparece en €/Miler (por millar), el precio_unitario es ese valor y la cantidad son unidades en miles
- Si no hay referencia de artículo, usa null
- Cargos de porte/transporte con importe explícito (ej: "Cargo / Transporte 198,00") SÍ forman 
  parte de la base imponible y deben sumarse; son distintos del material de embalaje fungible 
  (carros, alzas, lejas) que se ignora.
- Líneas con importe negativo (devoluciones de envases, safatas, bandejas) SÍ deben incluirse 
  en las líneas con subtotal negativo, ya que afectan al total de la factura.
- Líneas sin precio calculado (cantidad pero sin importe) ignóralas si el importe es 0 o vacío.


CASUÍSTICAS ESPECIALES:
- Facturas escaneadas (imágenes): extrae igualmente todos los campos visibles
- Facturas en catalán: "Data" = Fecha, "Albarà" = Albarán, "Forma de Pagament" = Forma de Pago, "Venciments" = Vencimientos, "Import" = Importe, "Descompte" = Descuento
- Facturas internacionales (holandés, francés): el IVA puede ser 0% (exento intracomunitario); en ese caso todas las bases van en base_imponible_21 (o el tipo que corresponda) e iva_21 = 0
- Facturas con "Importe Bruto" + descuento global: base_imponible = total_bruto - descuento
- Facturas multipágina con totales solo en última página: recorre todas las páginas para las líneas
- Facturas con página adicional de "pendientes de servir" o "artículos pendientes": 
  ignora completamente esa sección y sus importes. Los totales reales de la factura 
  están antes de esa sección, identificados por "SUMA B. IMPONIBLE", "TOTAL FACTURA", etc.


FORMATO DE SALIDA (JSON exacto):
{
  "proveedor": "nombre completo del proveedor emisor",
  "cif": "B12345678",
  "direccion_proveedor": "dirección completa del proveedor",
  "numero_factura": "000778/2026",
  "fecha": "2026-02-12",
  "moneda": "EUR",
  "numero_albaran": "3.342",
  "base_imponible_4": null,
  "base_imponible_10": 34.45,
  "base_imponible_21": 50.45,
  "iva_4": null,
  "iva_10": 3.44,
  "iva_21": 10.59,
  "total_bruto": 84.90,
  "total": 98.93,
  "forma_pago": "GIR BANCARI QUINZENAL",
  "vencimiento": "2026-02-13",
  "vencimientos": ["2026-02-13", "2026-03-13"],
  "lineas": [
    {
      "descripcion": "MIMOSA",
      "referencia": "147.487",
      "cantidad": 5,
      "precio_unitario": 3.25,
      "descuento_pct": 0,
      "iva_pct": 10,
      "subtotal": 16.25
    }
  ]
}

Ahora analiza el documento adjunto y extrae los datos en el mismo formato JSON.
"""
 
 
# ---------------------------------------------------------------------------
# Jerarquía de excepciones
# ---------------------------------------------------------------------------
 
class GeminiError(Exception):
    """Error base para fallos relacionados con Gemini."""
 
 
class GeminiErrorPermanente(GeminiError):
    """
    Error irrecuperable: no tiene sentido reintentar.
    El orquestador usa esta clase para decidir si descartar el email
    en lugar de volver a procesarlo en el siguiente ciclo.
    """
 
 
class GeminiQuotaError(GeminiErrorPermanente):
    """Sin cuota disponible (HTTP 429 / RESOURCE_EXHAUSTED)."""
 
 
class GeminiModelError(GeminiErrorPermanente):
    """Modelo no existe o no está disponible (HTTP 404 / NOT_FOUND)."""
 
 
class GeminiResponseError(GeminiError):
    """La respuesta de Gemini es inválida o no se puede parsear."""
 
 
# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------
 
class ExtractorGemini:
    DEFAULT_MODEL = "gemini-2.0-flash"
 
    def __init__(self) -> None:
        self._validar_configuracion()
        self._cliente = self._crear_cliente()
        self._modelo  = self._obtener_modelo()
        self._config  = self._crear_config()
        registro.info(f"ExtractorGemini iniciado con modelo: {self._modelo}")
 
 
    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------
 
    def _validar_configuracion(self) -> None:
        if not ajustes.clave_api_gemini:
            raise ValueError(
                "CLAVE_API_GEMINI no configurada. Añádela al archivo .env"
            )
 
    def _crear_cliente(self) -> genai.Client:
        return genai.Client(api_key=ajustes.clave_api_gemini)
 
    def _obtener_modelo(self) -> str:
        modelo = ajustes.modelo_gemini or self.DEFAULT_MODEL
        return modelo.replace("models/", "")
 
    def _crear_config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            temperature=self._validar_temperatura(ajustes.temperatura_gemini),
            response_mime_type="application/json",
        )
 
    @staticmethod
    def _validar_temperatura(valor: Any) -> float:
        if not isinstance(valor, (int, float)):
            raise ValueError("La temperatura debe ser numérica")
        if not 0 <= valor <= 2:
            raise ValueError("La temperatura debe estar entre 0 y 2")
        return float(valor)
 
 
    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
 
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        before_sleep=lambda estado: registro.warning(
            f"Reintento {estado.attempt_number}/3 de llamada a Gemini..."
        ),
    )
    def extraer_datos_factura(self, contenido_pdf: bytes) -> Dict[str, Any]:
        """
        Extrae los datos de una factura en PDF y los devuelve como diccionario.
 
        Lanza GeminiErrorPermanente (GeminiQuotaError / GeminiModelError)
        si el fallo no se puede resolver reintentando.
        """
        self._validar_pdf(contenido_pdf)
 
        registro.info(
            f"Enviando PDF a Gemini ({len(contenido_pdf) / 1024:.1f} KB)..."
        )
 
        respuesta    = self._llamar_api(contenido_pdf)
        texto        = self._extraer_texto_respuesta(respuesta)
        texto_limpio = self._limpiar_markdown(texto)
        datos        = self._parsear_json(texto_limpio)
 
        self._log_resultado(datos)
        return datos
 
    def extraer_desde_archivo(self, ruta_pdf: Union[str, Path]) -> Dict[str, Any]:
        """Extrae datos de un PDF almacenado en disco."""
        ruta = self._validar_ruta(ruta_pdf)
        return self.extraer_datos_factura(ruta.read_bytes())
 
 
    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
 
    @staticmethod
    def _validar_pdf(contenido_pdf: bytes) -> None:
        if not isinstance(contenido_pdf, bytes):
            raise TypeError("El contenido del PDF debe ser bytes")
        if not contenido_pdf:
            raise ValueError("El PDF está vacío")
 
    @staticmethod
    def _validar_ruta(ruta_pdf: Union[str, Path]) -> Path:
        if not isinstance(ruta_pdf, (str, Path)):
            raise TypeError("La ruta debe ser str o Path")
        ruta = Path(ruta_pdf)
        if not ruta.exists():
            raise FileNotFoundError(f"No existe el archivo: {ruta}")
        if not ruta.is_file():
            raise ValueError(f"La ruta no es un archivo válido: {ruta}")
        if ruta.suffix.lower() != ".pdf":
            raise ValueError("El archivo debe ser un PDF")
        return ruta
 
 
    # ------------------------------------------------------------------
    # Llamada a la API
    # ------------------------------------------------------------------
 
    def _llamar_api(self, contenido_pdf: bytes):
        try:
            contenido = types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(
                        data=contenido_pdf,
                        mime_type="application/pdf",
                    ),
                    types.Part.from_text(text=_PROMPT_EXTRACCION),  
                ],
            )
            return self._cliente.models.generate_content(
                model=self._modelo,
                contents=[contenido],
                config=self._config,
            )
        except ClientError as e:
            self._manejar_error_api(e)
        except Exception as e:
            registro.exception("Error inesperado en llamada a Gemini")
            raise GeminiError("Error inesperado en Gemini") from e
 
    def _manejar_error_api(self, error: ClientError) -> None:
        """
        Clasifica el error de la API y lanza la excepción adecuada.
 
        - 429 / RESOURCE_EXHAUSTED → GeminiQuotaError   (permanente)
        - 404 / NOT_FOUND          → GeminiModelError   (permanente)
        - resto                    → GeminiError         (genérico)
        """
        error_str = str(error)
 
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            registro.error(f"Sin cuota en Gemini (modelo: {self._modelo})")
            raise GeminiQuotaError("Sin cuota en Gemini") from error
 
        if "404" in error_str or "NOT_FOUND" in error_str:
            registro.error(f"Modelo no disponible: {self._modelo}")
            raise GeminiModelError(
                f"Modelo '{self._modelo}' no disponible. "
                "Comprueba MODELO_GEMINI en .env"
            ) from error
 
        registro.error(f"Error API Gemini: {error}")
        raise GeminiError("Error en API Gemini") from error
 
 
    # ------------------------------------------------------------------
    # Procesamiento de respuesta
    # ------------------------------------------------------------------
 
    @staticmethod
    def _extraer_texto_respuesta(respuesta: Any) -> str:
        if not respuesta or not getattr(respuesta, "text", None):
            raise GeminiResponseError("Respuesta vacía o inválida de Gemini")
        return respuesta.text.strip()
 
    @staticmethod
    def _limpiar_markdown(texto: str) -> str:
        """Elimina bloques ```json ... ``` si Gemini los incluye igualmente."""
        if texto.startswith("```"):
            lineas = texto.split("\n")
            if len(lineas) > 2:
                return "\n".join(lineas[1:-1])
        return texto
 
    @staticmethod
    def _parsear_json(texto: str) -> Dict[str, Any]:
        try:
            return json.loads(texto)
        except json.JSONDecodeError as error:
            registro.error(f"JSON inválido recibido de Gemini:\n{texto}")
            raise GeminiResponseError("JSON inválido de Gemini") from error
 
 
    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
 
    @staticmethod
    def _log_resultado(datos: Dict[str, Any]) -> None:
        registro.success(
            "Extracción exitosa: "
            f"{datos.get('proveedor')} | "
            f"Factura: {datos.get('numero_factura')} | "
            f"Total: {datos.get('total')}€"
        )