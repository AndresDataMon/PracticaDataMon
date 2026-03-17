class ProductView:
    # Mostrar mensaje de info y error
    def mostrar_mensaje(self, mensaje):
        print('[\x1b[32mINFO\x1b[0m]', mensaje)

    def mostrar_error(self, error):
        print('[\x1b[31mERROR\x1b[0m]', error)