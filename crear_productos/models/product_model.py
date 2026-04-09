import xmlrpc.client

from utils.image_util import encode_img_to_base64

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
            [[('name', 'not like', 'DUA VAT%')]],
            {
                'fields': campos,
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
            [[['name', '=', nombre]]],
            {
                'fields': campos,
            }
        )
    
    # Actualizar imagen de un producto
    def actualizar_imagen(self, id, nueva_imagen):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'write',
            [[id],
                {
                    'image_1920': encode_img_to_base64(nueva_imagen)
                }
            ],
        )

    # Obtener solo ciertos campos
    def listar_solo_algunos_campos(self, campos):
        return self.models.execute_kw(
            self.db,
            self.uid,
            self.password,
            'product.template',
            'search_read',
            [[('name', 'not like', 'DUA VAT%')]],
            {
                'fields': campos
            }
        )