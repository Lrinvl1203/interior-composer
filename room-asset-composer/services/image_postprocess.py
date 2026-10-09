from PIL import Image, ImageFilter


def crop_to_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return rgba
    return rgba.crop(bbox)


def feather_alpha(image: Image.Image, radius: int) -> Image.Image:
    rgba = image.convert("RGBA")
    radius = max(0, min(int(radius), 5))
    if radius == 0:
        return rgba
    red, green, blue, alpha = rgba.split()
    softened_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.merge("RGBA", (red, green, blue, softened_alpha))


def postprocess_image(
    image: Image.Image,
    auto_crop: bool = True,
    feather_radius: int = 0,
) -> Image.Image:
    result = image.convert("RGBA")
    if auto_crop:
        result = crop_to_alpha(result)
    if feather_radius:
        result = feather_alpha(result, feather_radius)
    return result
