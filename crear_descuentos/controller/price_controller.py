from model.price_model import PricelistModel

class PricelistController:
    def __init__(self, cfg):
        self.model = PricelistModel(cfg)

    def create_pricelist(nombre: str, currency_id: int, company_id: int = None, active: bool = True, sequence: int = 10, selectable: bool = True) -> any:
        datos = {
            "name": nombre,
            "currency_id": currency_id,
            "active": active,
            "sequence": sequence,
            "selectable": selectable
        }

        if company_id is not None:
            datos["company_id"] = company_id
        
        nuevo_id = self.model.crear_lista_de_precios(datos)

        print(f'Nueva lista de precios con ID: {nuevo_id}')