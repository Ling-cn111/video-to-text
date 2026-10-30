"""
从 public/icons/icon-1024.png 生成 Android 全套图标与启动图。

为什么不用 @capacitor/assets：它依赖 sharp 0.32（pnpm 因 build script 策略
未执行 postinstall，win32-x64 二进制缺失），改用 workspace 自带的 Pillow。

生成内容：
- mipmap-*/ic_launcher.png + ic_launcher_round.png：传统方形/圆形图标（48..192）
- mipmap-*/ic_launcher_foreground.png：自适应图标前景层（108dp 基准，四周留安全区）
- drawable/ic_launcher_background.xml 的底色：与图标背景一致，避免自适应图标出现白边
- drawable*/splash.png：启动图（白色底 + 居中图标）
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "icons" / "icon-1024.png"
RES = ROOT / "android" / "app" / "src" / "main" / "res"

# 图标背景色（取自 icon-1024.png 的主色，浅紫白）
BACKGROUND = (250, 248, 254, 255)

# 传统图标尺寸：mdpi..xxxhdpi
LAUNCHER_SIZES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}

# 自适应图标前景层：108dp 画布（mdpi 基准）
FOREGROUND_SIZES = {
    "mdpi": 108,
    "hdpi": 162,
    "xhdpi": 216,
    "xxhdpi": 324,
    "xxxhdpi": 432,
}

# 图标主色（取自 icon-1024.png 的紫色底），用于自适应图标背景与启动图
BRAND_COLOR = "#7C3AED"

# 启动图尺寸（宽 x 高）：竖屏 + 横屏 + 默认
SPLASH_SIZES = {
    "drawable": (480, 320),
    "drawable-land-mdpi": (480, 320),
    "drawable-land-hdpi": (800, 480),
    "drawable-land-xhdpi": (1280, 720),
    "drawable-land-xxhdpi": (1600, 960),
    "drawable-land-xxxhdpi": (1920, 1280),
    "drawable-port-mdpi": (320, 480),
    "drawable-port-hdpi": (480, 800),
    "drawable-port-xhdpi": (720, 1280),
    "drawable-port-xxhdpi": (960, 1600),
    "drawable-port-xxxhdpi": (1280, 1920),
}


def load_source() -> Image.Image:
    if not SRC.exists():
        raise SystemExit(f"找不到图标源文件：{SRC}")
    return Image.open(SRC).convert("RGBA")


def make_square(icon: Image.Image, size: int) -> Image.Image:
    """缩放到指定尺寸（源图本身就是方形）"""
    return icon.resize((size, size), Image.LANCZOS)


def make_round(icon: Image.Image, size: int) -> Image.Image:
    """圆形图标：先缩放到 4 倍尺寸再画遮罩，边缘更干净（避免锯齿）"""
    big = icon.resize((size * 4, size * 4), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    big.putalpha(mask)
    return big.resize((size, size), Image.LANCZOS)


def make_foreground(icon: Image.Image, canvas: int) -> Image.Image:
    """
    自适应图标前景层：整张图标铺满 108dp 画布。

    源图标本身是「满铺紫色底 + 居中白色图案」，图案只占画布中央约 43%，
    落在系统裁切安全区（中间 72dp）内。所以这里直接铺满即可，裁成圆形时
    切掉的只是紫色边缘，图案不会受影响。
    """
    return icon.resize((canvas, canvas), Image.LANCZOS)


def make_splash(icon: Image.Image, size: tuple[int, int]) -> Image.Image:
    """启动图：背景色铺底 + 居中图标（占短边 28%）"""
    width, height = size
    canvas = Image.new("RGBA", (width, height), BACKGROUND)
    side = round(min(width, height) * 0.28)
    logo = icon.resize((side, side), Image.LANCZOS)
    canvas.paste(logo, ((width - side) // 2, (height - side) // 2), logo)
    return canvas


def main() -> None:
    icon = load_source()
    written = 0

    for density, size in LAUNCHER_SIZES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_square(icon, size).save(folder / "ic_launcher.png", optimize=True)
        make_round(icon, size).save(folder / "ic_launcher_round.png", optimize=True)
        written += 2

    for density, size in FOREGROUND_SIZES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_foreground(icon, size).save(folder / "ic_launcher_foreground.png", optimize=True)
        written += 1

    for folder_name, size in SPLASH_SIZES.items():
        folder = RES / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        make_splash(icon, size).save(folder / "splash.png", optimize=True)
        written += 1

    # 自适应图标底色与图标主色一致：默认模板是纯白 #FFFFFF，
    # 前景层铺满后看不到，但系统在裁切动画里会露出这一层，白色会闪一下。
    (RES / "values" / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        f'    <color name="ic_launcher_background">{BRAND_COLOR}</color>\n'
        "</resources>\n",
        encoding="utf-8",
    )
    written += 1

    print(f"[icons] 已生成 {written} 个文件到 {RES}")


if __name__ == "__main__":
    main()
