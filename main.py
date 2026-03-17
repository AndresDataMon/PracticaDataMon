import config

from controllers.product_controller import ProductController

cfg = config.current_config
controller = ProductController(cfg)

csv_file = "resources/data/productos_odoo_20.csv"
image_folder = "resources/imgs"

# controller.create_products_from_csv(csv_file, image_folder)
# print("\n\n")
# controller.list_all_products()
# print("\n\n")
# controller.delete_a_product_by_id(91)
controller.search_product_by_name('Camiseta')