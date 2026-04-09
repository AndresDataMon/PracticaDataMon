import config

from controllers.product_controller import ProductController

def main():
    cfg = config.current_config
    controller = ProductController(cfg)

    csv_file = 'resources/data/productos_odoo_20.csv'
    image_folder = 'resources/imgs'

    opcion = -1

    while opcion != 0:
        print('\n¿Qué quieres hacer?\n')
        print('0. Salir.')
        print('1. Crear productos desde un CSV.')
        print('2. Listar TODOS los productos.')
        print('3. Borrar un producto por ID.')
        print('4. Buscar un producto por nombre.')
        print('5. Actualizar la imagen de un producto.')
        print('6. Listar SOLO el nombre, precio de venta y código de referencia interna.')

        opcion = int(input('>> '))

        match opcion:
            case 1:
                controller.create_products_from_csv(csv_file, image_folder)

            case 2:
                controller.list_all_products()

            case 3:
                id = int(input('Introduce el ID a borrar: '))
                controller.delete_a_product_by_id(id)

            case 4:
                nombre_producto = input('Introduce el nombre del producto a buscar: ')
                controller.search_product_by_name(nombre_producto)

            case 5:
                imagen = input('Nueva imagen (camiseta.jpg ó camiseta2.jpg): ')
                nueva_imagen = image_folder + "/" + imagen

                id = int(input('Introduce el ID a actualizar: '))
                controller.update_product_image(id, nueva_imagen)

            case 6:
                controller.list_some_fields()

            case 0:
                opcion = 0
                break

            case _:
                print(f'\'{opcion}\' no es correcto. Intentalo otra vez.\n\n')


if __name__ == "__main__":
    main()