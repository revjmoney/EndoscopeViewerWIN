"""A clearly labeled test card for screenshots; no personal camera footage."""
from PIL import Image, ImageDraw, ImageFont


def demo_image():
    image = Image.new("RGB", (960, 640), "#101c22")
    draw = ImageDraw.Draw(image)
    def font(size):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/consola.ttf", size)
        except OSError:
            return ImageFont.load_default()
    for x in range(0, 960, 40):
        draw.line((x, 0, x, 640), fill="#1a3038")
    for y in range(0, 640, 40):
        draw.line((0, y, 960, y), fill="#1a3038")
    draw.rounded_rectangle((45, 40, 915, 600), radius=24, fill="#182b33", outline="#395764", width=2)
    draw.text((80, 70), "ENDOSCOPEVIEWERWIN", font=font(42), fill="#69e6bd")
    draw.text((82, 130), "THE FORBIDDEN USB NOODLE", font=font(24), fill="#e8eff2")
    draw.line([(120, 430), (180, 430), (250, 300), (360, 300), (440, 430), (550, 430), (640, 310)], fill="#070d11", width=38, joint="curve")
    draw.line([(120, 425), (180, 425), (250, 295), (360, 295), (440, 425), (550, 425), (640, 305)], fill="#547482", width=22, joint="curve")
    draw.rounded_rectangle((620, 205, 800, 340), radius=40, fill="#273f4b", outline="#9ab9c4", width=4)
    draw.ellipse((650, 230, 770, 320), fill="#07141b", outline="#69e6bd", width=5)
    draw.ellipse((675, 249, 706, 288), fill="#f5f7f7")
    draw.ellipse((722, 249, 753, 288), fill="#f5f7f7")
    draw.ellipse((688, 261, 701, 278), fill="#13212a")
    draw.ellipse((735, 261, 748, 278), fill="#13212a")
    draw.text((80, 495), '"Android only" was a suggestion.', font=font(27), fill="#f1c478")
    draw.rounded_rectangle((80, 548, 518, 580), radius=8, fill="#314752")
    draw.text((93, 553), "DEMO IMAGE / NOT LIVE CAMERA FOOTAGE", font=font(17), fill="#d7e4e9")
    return image
