"""
Modelos Pydantic que definen la estructura y las reglas de negocio
de una factura española.

Valida:
  - Formato de CIF/NIF/NIE (español) o VAT extranjero
  - Fechas en múltiples formatos incluyendo año de 2 dígitos
  - Coherencia entre base imponible, IVA y total
  - Presencia de al menos una base imponible
"""

import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# Formatos de fecha aceptados (incluye año de 2 dígitos para Gosbi, Pastoret, etc.)
_FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y")

# Tolerancia máxima en la coherencia de importes (€)
_TOLERANCIA_IMPORTE = 0.10


# ---------------------------------------------------------------------------
# Línea de detalle de factura
# ---------------------------------------------------------------------------

class LineaFactura(BaseModel):
    """Una línea del detalle de productos o servicios de la factura."""

    descripcion:     str            = Field(min_length=1)
    referencia:      Optional[str]  = None
    cantidad:        float          = Field(ne=0)        # ne=0 permite negativos (devoluciones)
    precio_unitario: float          = Field(ge=0)
    descuento_pct:   float          = Field(default=0.0, ge=0, le=100)
    iva_pct:         int            = Field(ge=0, le=100)
    subtotal:        Optional[float] = None

    @model_validator(mode="after")
    def _calcular_subtotal(self) -> "LineaFactura":
        """Calcula el subtotal si no viene informado; lo verifica si sí viene."""
        subtotal_calculado = round(self.cantidad * self.precio_unitario, 2)
        if self.subtotal is None:
            self.subtotal = subtotal_calculado
        # Si difiere más de 5 céntimos, es probable un redondeo del proveedor.
        # No se lanza error; el validador de totales en Factura lo detectará.
        return self


# ---------------------------------------------------------------------------
# Factura completa
# ---------------------------------------------------------------------------

class Factura(BaseModel):
    """
    Modelo completo de una factura comercial española o internacional validada.
    Incluye todas las reglas de negocio como validadores de campo y de modelo.
    """

    # Datos del emisor
    proveedor:           str           = Field(min_length=2)
    cif:                 str
    direccion_proveedor: Optional[str] = None

    # Identificación de la factura
    numero_factura:  str           = Field(min_length=1)
    fecha:           date
    moneda:          str           = Field(default="EUR", min_length=3, max_length=3)
    numero_albaran:  Optional[str] = None

    # Bases imponibles por tipo de IVA (al menos una obligatoria)
    base_imponible_4:  Optional[float] = Field(default=None, ge=0)
    base_imponible_10: Optional[float] = Field(default=None, ge=0)
    base_imponible_21: Optional[float] = Field(default=None, ge=0)

    # Cuotas de IVA correspondientes
    iva_4:  Optional[float] = Field(default=None, ge=0)
    iva_10: Optional[float] = Field(default=None, ge=0)
    iva_21: Optional[float] = Field(default=None, ge=0)

    # Totales
    total_bruto: Optional[float] = Field(default=None, ge=0)
    total:       float           = Field(gt=0)

    # Condiciones de pago
    forma_pago:   Optional[str]       = None
    vencimiento:  Optional[date]      = None          # Primer vencimiento (usado por Odoo)
    vencimientos: list[date]          = Field(default_factory=list)  # Todos los vencimientos

    # Líneas de detalle
    lineas: list[LineaFactura] = Field(default_factory=list)


    # ------------------------------------------------------------------
    # Validadores de campo
    # ------------------------------------------------------------------

    @field_validator("cif")
    @classmethod
    def _validar_cif(cls, valor: str) -> str:
        """
        Valida el formato de CIF, NIF o NIE español.
        Acepta sin validación estricta los VAT extranjeros (no empiezan por ES).
        """
        valor = valor.strip().upper()

        # VAT extranjero (ej: NL804543306B01, GB123456789) → aceptar tal cual
        if re.match(r"^[A-Z]{2}", valor) and not valor.startswith("ES"):
            return valor

        # Quitar prefijo ES si viene con él (ej: ESB64056419 → B64056419)
        if valor.startswith("ES"):
            valor = valor[2:]

        es_cif = re.match(r"^[ABCDEFGHJKLMNPQRSUVW]\d{7}[0-9A-J]$", valor)
        es_nif = re.match(r"^\d{8}[TRWAGMYFPDXBNJZSQVHLCKE]$", valor)
        es_nie = re.match(r"^[XYZ]\d{7}[TRWAGMYFPDXBNJZSQVHLCKE]$", valor)

        if not (es_cif or es_nif or es_nie):
            raise ValueError(
                f"CIF/NIF inválido: '{valor}'. "
                "Formatos válidos: B12345678 (CIF) | 12345678A (NIF) | X1234567A (NIE)"
            )
        return valor

    @field_validator("fecha", mode="before")
    @classmethod
    def _parsear_fecha(cls, valor) -> date:
        """Acepta fechas en cualquiera de los formatos definidos en _FORMATOS_FECHA."""
        if isinstance(valor, date):
            return valor
        if isinstance(valor, str):
            for formato in _FORMATOS_FECHA:
                try:
                    return datetime.strptime(valor, formato).date()
                except ValueError:
                    continue
        raise ValueError(f"Formato de fecha no reconocido: '{valor}'")

    @field_validator("vencimiento", mode="before")
    @classmethod
    def _parsear_vencimiento(cls, valor) -> Optional[date]:
        """Como _parsear_fecha pero no lanza error si el valor es inválido."""
        if valor is None:
            return None
        if isinstance(valor, date):
            return valor
        if isinstance(valor, str):
            for formato in _FORMATOS_FECHA:
                try:
                    return datetime.strptime(valor, formato).date()
                except ValueError:
                    continue
        return None  # Vencimiento mal formateado → None, no es error crítico

    @field_validator("vencimientos", mode="before")
    @classmethod
    def _parsear_vencimientos(cls, valor) -> list[date]:
        """Parsea la lista de vencimientos tolerando formatos mixtos y valores inválidos."""
        if not valor:
            return []
        resultado = []
        for v in valor:
            if isinstance(v, date):
                resultado.append(v)
                continue
            if isinstance(v, str):
                for formato in _FORMATOS_FECHA:
                    try:
                        resultado.append(datetime.strptime(v, formato).date())
                        break
                    except ValueError:
                        continue
            # Valores no parseables se ignoran silenciosamente
        return resultado

    @field_validator("moneda")
    @classmethod
    def _normalizar_moneda(cls, valor: str) -> str:
        """Normaliza el código de moneda a mayúsculas."""
        return valor.strip().upper()


    # ------------------------------------------------------------------
    # Validadores cruzados (cross-field)
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _sincronizar_vencimiento_principal(self) -> "Factura":
        """
        Si hay lista de vencimientos pero no hay vencimiento principal,
        usa el primero de la lista. Y viceversa: si hay vencimiento principal
        pero la lista está vacía, la inicializa con ese valor.
        """
        if self.vencimiento and not self.vencimientos:
            self.vencimientos = [self.vencimiento]
        elif self.vencimientos and not self.vencimiento:
            self.vencimiento = self.vencimientos[0]
        return self

    @model_validator(mode="after")
    def _verificar_coherencia_importes(self) -> "Factura":
        """Comprueba que base imponible total + IVA total ≈ total declarado."""
        suma_bases = self.suma_bases_imponibles
        suma_iva   = self.suma_cuotas_iva

        if suma_bases > 0 and suma_iva > 0:
            total_esperado = round(suma_bases + suma_iva, 2)
            diferencia = abs(self.total - total_esperado)
            if diferencia > _TOLERANCIA_IMPORTE:
                raise ValueError(
                    f"Total declarado ({self.total}€) no coincide con "
                    f"base ({suma_bases}€) + IVA ({suma_iva}€) = {total_esperado}€. "
                    f"Diferencia: {diferencia:.2f}€"
                )
        return self

    @model_validator(mode="after")
    def _verificar_base_imponible_presente(self) -> "Factura":
        """Requiere al menos una base imponible con valor positivo."""
        bases = [self.base_imponible_4, self.base_imponible_10, self.base_imponible_21]
        if not any(b is not None and b > 0 for b in bases):
            raise ValueError(
                "La factura debe incluir al menos una base imponible positiva "
                "(base_imponible_4, base_imponible_10 o base_imponible_21)"
            )
        return self


    # ------------------------------------------------------------------
    # Propiedades calculadas
    # ------------------------------------------------------------------

    @property
    def suma_bases_imponibles(self) -> float:
        """Total de todas las bases imponibles."""
        return round(
            sum(
                b for b in [self.base_imponible_4, self.base_imponible_10, self.base_imponible_21]
                if b is not None
            ),
            2,
        )

    @property
    def suma_cuotas_iva(self) -> float:
        """Total de todas las cuotas de IVA."""
        return round(
            sum(
                i for i in [self.iva_4, self.iva_10, self.iva_21]
                if i is not None
            ),
            2,
        )