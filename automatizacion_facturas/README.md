# invoice_automation

> Pipeline automatizado: **Gmail → Gemini AI → Odoo**

Lee facturas PDF recibidas por correo, extrae sus datos con IA y crea pedidos de compra en Odoo automáticamente.

## Inicio rápido

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar credenciales
cp .env
# Editar .env con tus valores

# 3. Verificar configuración
python main.py --test-config

# 4. Probar con un PDF local
python main.py --file data/samples/factura.pdf --no-odoo

# 5. Arrancar el monitor completo
python main.py
```

## Documentación completa

Ver [`docs/README.md`](docs/README.md) para:
- Configuración detallada de Gmail (OAuth2)
- Configuración de Gemini API
- Configuración de Odoo
- Resolución de problemas
- Referencia de todos los módulos

## Estructura

```
src/gmail/      ← Monitor de Gmail + extracción de PDFs
src/gemini/     ← Procesamiento con Gemini AI
src/validator/  ← Validación de datos con Pydantic
src/odoo/       ← Integración con Odoo XML-RPC
utils/          ← Logger, base de datos de auditoría
config/         ← Configuración centralizada
```

## Comandos

| Comando | Descripción |
|---------|-------------|
| `python main.py` | Monitor Gmail continuo |
| `python main.py --file factura.pdf` | Procesar PDF local |
| `python main.py --file factura.pdf --no-odoo` | Solo extracción + validación |
| `python main.py --stats` | Ver estadísticas |
| `python main.py --test-config` | Verificar configuración |
| `pytest tests/ -v` | Ejecutar tests |
