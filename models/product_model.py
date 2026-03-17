import xmlrpc.client

class ProductModel:
    # Constructor
    def __init__(self, cfg):
        self.url = cfg.get('odoo_url')
        self.db = cfg.get('odoo_db')
        self.username = cfg.get('odoo_user')
        self.password = cfg.get('odoo_pass')

        self.common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
        self.uid = self.common.authenticate(self.db, self.username, self.password, {})
        self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')

        if not self.uid:
            raise Exception("No se pudo autenticar con Odoo")
        
    # Crear un producto en base a un diccionario
    def crear_producto(self, producto):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'create',
            [producto]
        )
    
    # Listar todos los productos creados
    def listar_todos_productos(self, campos):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'search_read',
            [[]],
            {
                'fields': campos
            }
        )
    
    # Borrar un producto por ID
    def borrar_producto_por_id(self, id):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'unlink',
            [[id]]
        )
    
    # Buscar por un nombre específico
    def buscar_por_nombre(self, nombre, campos):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'search_read',
            [[nombre]],
            {
                'fields': campos
            }
        )