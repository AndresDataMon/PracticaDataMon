import config

from controllers.product_controller import ProductController

cfg = config.current_config
controller = ProductController(cfg)

csv_file = "resources/data/productos_odoo_20.csv"
image_folder = "resources/imgs"

controller.create_products_from_csv(csv_file, image_folder)