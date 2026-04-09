import os
import base64

def encode_img_to_base64(path_to_image):
    if not os.path.exists(path_to_image):
        return None
    
    with open(path_to_image, 'rb') as img:
        return base64.b64encode(img.read()).decode('utf-8')