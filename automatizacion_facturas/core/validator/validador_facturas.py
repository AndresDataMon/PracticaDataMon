"""
Valida los datos crudos extraídos por Gemini contra el esquema Pydantic.
Centraliza la lógica de validación y produce resultados tipados.
"""

import re
from pydantic import ValidationError

from utils.registro import registro
from .esquema_factura import Factura


# Campos obligatorios que se verifican antes de Pydantic
_CAMPOS_OBLIGATORIOS = ["proveedor", "cif", "numero_factura", "fecha", "total"]

# Tolerancia para la coherencia líneas vs bases imponibles (€)
_TOLERANCIA_LINEAS = 0.50


class ResultadoValidacion:
    """
    Encapsula el resultado de validar un diccionario de factura.

    Attributes:
        es_valido: True si los datos superaron todas las validaciones.
        factura:   Objeto Factura validado (solo si es_valido es True).
        errores:   Lista de mensajes de error (solo si es_valido es False).
        avisos:    Lista de advertencias no bloqueantes (siempre disponible).
    """

    def __init__(
        self,
        es_valido: bool,
        factura: Factura | None = None,
        errores: list[str] | None = None,
        avisos: list[str] | None = None,
    ):
        self.es_valido = es_valido
        self.factura   = factura
        self.errores   = errores or []
        self.avisos    = avisos  or []

    def __repr__(self) -> str:
        if self.es_valido:
            return f"<ResultadoValidacion OK: {self.factura.numero_factura}>"
        return f"<ResultadoValidacion FALLIDO: {self.errores}>"


class ValidadorFacturas:
    """
    Valida diccionarios de datos de factura contra el esquema Factura.

    Uso:
        validador = ValidadorFacturas()
        resultado = validador.validar(datos_crudos)
        if resultado.es_valido:
            factura = resultado.factura
    """

    def validar(self, datos_crudos: dict) -> ResultadoValidacion:
        """
        Valida un diccionario de datos de factura en tres pasos:
          1. Verificación rápida de campos obligatorios
          2. Normalización de tipos (strings numéricos, nulls como string, etc.)
          3. Validación completa con Pydantic
          4. Avisos de coherencia no bloqueantes (líneas vs bases imponibles)

        Args:
            datos_crudos: dict con los campos extraídos por Gemini.

        Returns:
            ResultadoValidacion con la factura validada o los errores.
        """
        if not isinstance(datos_crudos, dict):
            return ResultadoValidacion(
                es_valido=False,
                errores=["Los datos recibidos no son un diccionario válido."],
            )

        campos_faltantes = [
            campo for campo in _CAMPOS_OBLIGATORIOS
            if not datos_crudos.get(campo)
        ]
        if campos_faltantes:
            return ResultadoValidacion(
                es_valido=False,
                errores=[f"Campos obligatorios ausentes: {', '.join(campos_faltantes)}."],
            )

        datos_normalizados = self._normalizar(datos_crudos)

        try:
            factura = Factura.model_validate(datos_normalizados)
            avisos  = self._verificar_coherencia_lineas(factura)

            for aviso in avisos:
                registro.warning(aviso)

            registro.success(
                f"Factura válida: {factura.proveedor} | "
                f"{factura.numero_factura} | {factura.total}€"
            )
            return ResultadoValidacion(es_valido=True, factura=factura, avisos=avisos)

        except ValidationError as error:
            errores = self._formatear_errores_pydantic(error)
            registro.warning(
                f"Factura inválida ({len(errores)} error(es)):\n"
                + "\n".join(f"  - {e}" for e in errores)
            )
            return ResultadoValidacion(es_valido=False, errores=errores)


    # ------------------------------------------------------------------
    # Normalización de datos crudos
    # ------------------------------------------------------------------

    def _normalizar(self, datos: dict) -> dict:
        """
        Limpia y normaliza los datos crudos de Gemini antes de pasarlos a Pydantic.

        Problemas habituales que corrige:
          - Strings "null" / "none" / "" → None
          - Importes con coma decimal ("98,93") → float (98.93)
          - Campos numéricos llegados como string ("98.93") → float
          - Listas de líneas con entradas None filtradas
        """
        resultado = {}
        campos_numericos = {
            "base_imponible_4", "base_imponible_10", "base_imponible_21",
            "iva_4", "iva_10", "iva_21",
            "total_bruto", "total",
        }
        campos_numericos_linea = {"cantidad", "precio_unitario", "descuento_pct", "subtotal", "iva_pct"}

        for clave, valor in datos.items():
            if clave == "lineas":
                resultado["lineas"] = self._normalizar_lineas(valor, campos_numericos_linea)
            elif clave in campos_numericos:
                resultado[clave] = self._normalizar_importe(valor)
            else:
                resultado[clave] = self._normalizar_valor(valor)

        return resultado

    @staticmethod
    def _normalizar_valor(valor) -> object:
        """Convierte strings "null"/"none"/"" a None."""
        if isinstance(valor, str) and valor.strip().lower() in ("null", "none", ""):
            return None
        return valor

    @staticmethod
    def _normalizar_importe(valor) -> float | None:
        if valor is None:
            return None
        if isinstance(valor, (int, float)):
            return float(valor)
        if isinstance(valor, str):
            valor = valor.strip()
            if valor.lower() in ("null", "none", ""):
                return None
            # Eliminar símbolo de moneda, porcentaje y espacios
            valor = re.sub(r"[€$£%\s]", "", valor)  
            if re.match(r"^\d{1,3}(\.\d{3})*(,\d+)?$", valor):
                valor = valor.replace(".", "").replace(",", ".")
            elif "," in valor and "." not in valor:
                valor = valor.replace(",", ".")
            try:
                return float(valor)
            except ValueError:
                return None
        return None

    def _normalizar_lineas(self, lineas, campos_numericos: set) -> list:
        """Filtra entradas None y normaliza los campos numéricos de cada línea."""
        if not isinstance(lineas, list):
            return []
        resultado = []
        for linea in lineas:
            if not isinstance(linea, dict):
                continue
            linea_normalizada = {}
            for clave, valor in linea.items():
                if clave in campos_numericos:
                    linea_normalizada[clave] = self._normalizar_importe(valor)
                else:
                    linea_normalizada[clave] = self._normalizar_valor(valor)
            resultado.append(linea_normalizada)
        return resultado


    # ------------------------------------------------------------------
    # Avisos de coherencia no bloqueantes
    # ------------------------------------------------------------------

    @staticmethod
    def _verificar_coherencia_lineas(factura: Factura) -> list[str]:
        """
        Comprueba que la suma de subtotales de líneas ≈ suma de bases imponibles.
        No es bloqueante: descuentos globales, portes o líneas incompletas
        pueden causar diferencias legítimas.

        Returns:
            Lista de avisos (vacía si todo es coherente).
        """
        if not factura.lineas:
            return []

        suma_lineas = round(
            sum(linea.subtotal for linea in factura.lineas if linea.subtotal is not None),
            2,
        )
        suma_bases = factura.suma_bases_imponibles

        if suma_bases == 0:
            return []

        diferencia = abs(suma_lineas - suma_bases)
        if diferencia > _TOLERANCIA_LINEAS:
            return [
                f"Coherencia líneas: suma de subtotales ({suma_lineas}€) difiere "
                f"de la suma de bases imponibles ({suma_bases}€) en {diferencia:.2f}€. "
                "Posible descuento global, porte o línea no capturada."
            ]
        return []


    # ------------------------------------------------------------------
    # Formateo de errores
    # ------------------------------------------------------------------

    @staticmethod
    def _formatear_errores_pydantic(error: ValidationError) -> list[str]:
        """Convierte los errores de Pydantic en mensajes legibles."""
        mensajes = []
        for detalle in error.errors():
            ubicacion = detalle.get("loc", ())
            campo = str(ubicacion[0]) if ubicacion else "modelo"
            mensajes.append(f"Campo '{campo}': {detalle['msg']}")
        return mensajes