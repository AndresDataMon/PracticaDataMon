import config

from controller.price_controller import PricelistController

def main():
    cfg = config.current_config
    controller = PricelistController(cfg=cfg)

    controller.create_pricelist('Descuento Navidad', 126, 1, True)

if __name__ == "__main__":
    main()