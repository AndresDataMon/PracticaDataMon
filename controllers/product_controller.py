import os
import csv
import json

from models.product_model import ProductModel
from views.product_view import ProductView
from utils.image_util import encode_img_to_base64

class ProductController:
    # Constructor
    def __init__(self, cfg):
        self.model = ProductModel(cfg)
        self.view = ProductView()

    # Crear productos desde .csv
    def create_products_from_csv(self, path_to_cvs, image_folder=None):
        if not os.path.exists(path_to_cvs):
            self.view.mostrar_error(f'No se pudo encontrar el .csv: {path_to_cvs}')
            return

        with open(path_to_cvs, newline='', encoding='utf-8') as csv_file:
            reader = csv.DictReader(csv_file)

            for row in reader:
                # Convertir campos numéricos y listas según corresponda
                product = {
                    'name': row.get('name'),
                    'default_code': row.get('default_code'),
                    'barcode': row.get('barcode'),
                    'list_price': float(row.get('list_price')),
                    'standard_price': float(row.get('standard_price')),
                    'type': row.get('type'),
                    'categ_id': int(row.get('categ_id', 1)),
                    'purchase_ok': True,
                    'sale_ok': True
                }

                # Si existe, se añada una imagen
                if image_folder and row.get('image_file'):
                    image_path = os.path.join(image_folder, row['image_file'])
                    product['image_1920'] = encode_img_to_base64(image_path)

                product_id = self.model.crear_producto(product)
                self.view.mostrar_mensaje(f'Producto creado: {product['name']} | Producto ID: {product_id}')

    # Listar todos los productos
    def list_all_products(self):
        campos = ['name', 'default_code', 'barcode', 'list_price', 'standard_price', 'type', 'categ_id', 'purchase_ok', 'sale_ok']
        productos = self.model.listar_todos_productos(campos)

        for producto in productos:
            producto_con_formato = json.dumps(producto, indent=4, sort_keys=True)
            print(producto_con_formato)

    # Borrar un producto por ID
    def delete_a_product_by_id(self, id):
        borrado = self.model.borrar_producto_por_id(id)

        if borrado:
            self.view.mostrar_mensaje(f'Producto con ID: {id} ha sido borrado con éxito')
        else:
            self.view.mostrar_error(f'No se puedo borrar el producto con ID: {id}')

    # Mostrar detalles de un producto por nombre
    def search_product_by_name(self, name):
        campos = ['name', 'default_code', 'barcode', 'list_price', 'standard_price', 'type', 'categ_id', 'purchase_ok', 'sale_ok']
        productos = self.model.buscar_por_nombre(name, campos)

        for producto in productos:
            producto_con_formato = json.dumps(producto, indent=4, sort_keys=True)
            print(producto_con_formato)