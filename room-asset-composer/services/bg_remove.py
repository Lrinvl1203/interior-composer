from io import BytesIO

from PIL import Image
from rembg import new_session, remove


MODEL_NAME = "isnet-general-use"


def create_session():
    return new_session(MODEL_NAME)


def remove_background(image: Image.Image, session=None) -> Image.Image:
    source = image.convert("RGBA")
    result = remove(source, session=session, post_process_mask=True)
    if not isinstance(result, Image.Image):
        result = Image.open(BytesIO(result))
    return result.convert("RGBA")
