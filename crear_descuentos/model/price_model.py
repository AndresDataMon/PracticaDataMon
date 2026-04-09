import xmlrpc.client

class PricelistModel:
    def __init__(self, cfg: dict):
        self.url = cfg.get('odoo_url')
        self.db = cfg.get('odoo_db')
        self.username = cfg.get('odoo_user')
        self.password = cfg.get('odoo_pass')

        self.common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
        self.uid = self.common.authenticate(self.db, self.username, self.password, {})
        self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')

        if not self.uid:
            raise Exception("No se pudo autenticar con Odoo")
        
    # Crear una lista de precios
    def crear_lista_de_precios(self, lista_precios: dict):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.pricelist',
            'create',
            [lista_precios]
        )