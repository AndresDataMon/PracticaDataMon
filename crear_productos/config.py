import os

from dotenv import load_dotenv

load_dotenv()

config = {
    'odoo_url': os.getenv("ODOO_URL"),
    'odoo_db': os.getenv("ODOO_DB"),
    'odoo_user': os.getenv("ODOO_USER"),
    'odoo_pass': os.getenv("ODOO_PASS")
}

current_config = config