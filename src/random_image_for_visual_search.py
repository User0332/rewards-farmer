import io
import json
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from PIL import Image


# ============================================================
# CONFIG
# ============================================================

API_URL = "https://commons.wikimedia.org/w/api.php"

OUTPUT_FILE = Path("visual_search.jpg")
METADATA_FILE = Path("visual_search.json")

MAX_ATTEMPTS = 10

# Loose filtering
MIN_WIDTH = 300
MIN_HEIGHT = 300

# Maximum original file size we'll accept
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

# Wikimedia will generate a thumbnail around this width
THUMBNAIL_WIDTH = 1280

# Normal delay between failed attempts
REQUEST_DELAY = 3

# Maximum rate-limit wait
MAX_BACKOFF = 300


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "RandomVisualSearchImage/1.1 "
        "(contact: 12345rfdz@gmail.com)"
    )
})


# ============================================================
# URL HELPERS
# ============================================================

def clean_url(url):
    """Remove query parameters from Wikimedia URLs."""

    parts = urlsplit(url)

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        "",
        "",
    ))


# ============================================================
# RATE LIMIT HANDLING
# ============================================================

def wait_after_429(response, attempt):
    """Wait according to Wikimedia's Retry-After header."""

    retry_after = response.headers.get("Retry-After")

    if retry_after:
        try:
            wait_time = int(retry_after)
        except ValueError:
            wait_time = min(
                2 ** attempt,
                MAX_BACKOFF,
            )
    else:
        wait_time = min(
            2 ** attempt,
            MAX_BACKOFF,
        )

    wait_time = max(5, wait_time)

    print(
        f"Rate limited. Waiting "
        f"{wait_time} seconds..."
    )

    time.sleep(wait_time)


# ============================================================
# DOWNLOAD
# ============================================================

def download_image(url):
    """Download image bytes from Wikimedia."""

    url = clean_url(url)

    try:
        response = session.get(
            url,
            timeout=30,
            allow_redirects=True,
        )

    except (requests.RequestException, ConnectionResetError, OSError) as e:
        print(f"Download failed: {e}")
        return None

    if response.status_code == 429:
        wait_after_429(response, 1)
        return None

    if response.status_code == 403:
        print("Wikimedia returned 403 Forbidden.")
        return None

    try:
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"HTTP error: {e}")
        return None

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    if not content_type.startswith("image/"):
        print(
            f"Not an image: {content_type}"
        )
        return None

    if not response.content:
        print("Downloaded image is empty.")
        return None

    return response.content


# ============================================================
# JPEG CONVERSION
# ============================================================

def convert_to_jpeg(image_data):
    """Convert downloaded image bytes to JPEG."""

    try:
        with Image.open(
            io.BytesIO(image_data)
        ) as image:

            # JPEG does not support alpha (transparency).
            # If the image has transparency (RGBA or LA), paste it over a white background.
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                background.paste(image, mask=image.split()[-1])
                jpeg_image = background
            else:
                jpeg_image = image.convert("RGB")

            output = io.BytesIO()

            jpeg_image.save(
                output,
                format="JPEG",
                quality=90,
                optimize=True,
            )

            return output.getvalue()

    except Exception as e:
        print(
            f"JPEG conversion failed: {e}"
        )
        return None


def generate_fallback_image():
    """Generate a synthetic local JPEG image using PIL as a fallback."""
    print("[INFO] Generating synthetic local fallback image for visual search...")
    from PIL import ImageDraw
    import random

    img = Image.new("RGB", (800, 600), color=(random.randint(50, 200), random.randint(50, 200), random.randint(50, 200)))
    draw = ImageDraw.Draw(img)
    for _ in range(10):
        x0 = random.randint(0, 700)
        y0 = random.randint(0, 500)
        x1 = x0 + random.randint(50, 200)
        y1 = y0 + random.randint(50, 200)
        fill = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        draw.rectangle([x0, y0, x1, y1], fill=fill)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    jpeg_data = output.getvalue()

    OUTPUT_FILE.write_bytes(jpeg_data)
    print(f"Saved fallback image to {OUTPUT_FILE.absolute()}")
    return {"title": "Fallback Synthetic Image", "width": 800, "height": 600}


# ============================================================
# RANDOM IMAGE
# ============================================================

def get_random_image():

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        if attempt > 1:
            time.sleep(REQUEST_DELAY)

        print(
            f"\nAttempt "
            f"{attempt}/{MAX_ATTEMPTS}"
        )

        params = {
            "action": "query",
            "format": "json",

            "generator": "random",
            "grnnamespace": 6,
            "grnlimit": 1,

            "prop": "imageinfo",

            "iiprop": (
                "url|size|mime|dimensions"
            ),

            "iiurlwidth": THUMBNAIL_WIDTH,
        }

        try:
            response = session.get(
                API_URL,
                params=params,
                timeout=20,
            )

        except (requests.RequestException, ConnectionResetError, OSError) as e:
            print(f"API request failed: {e}")
            continue

        if response.status_code == 429:
            wait_after_429(
                response,
                attempt,
            )
            continue

        try:
            response.raise_for_status()
            data = response.json()

        except (
            requests.RequestException,
            ValueError,
        ) as e:
            print(f"API error: {e}")
            continue

        pages = (
            data
            .get("query", {})
            .get("pages", {})
        )

        if not pages:
            print("No page returned.")
            continue

        page = next(
            iter(pages.values())
        )

        title = page.get(
            "title",
            "Unknown",
        )

        imageinfo = page.get(
            "imageinfo"
        )

        if not imageinfo:
            print(
                "No image information."
            )
            continue

        info = imageinfo[0]

        mime = info.get(
            "mime",
            "",
        )

        width = info.get(
            "width",
            0,
        )

        height = info.get(
            "height",
            0,
        )

        size = info.get(
            "size",
            0,
        )

        thumbnail_url = info.get(
            "thumburl"
        )

        original_url = info.get(
            "url"
        )

        if mime not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            print(
                f"Skipping unsupported type: "
                f"{mime}"
            )
            continue

        if width < MIN_WIDTH or height < MIN_HEIGHT:
            print(
                f"Skipping small image: "
                f"{width}x{height}"
            )
            continue

        if size > MAX_FILE_SIZE:
            print(
                f"Skipping large image: "
                f"{size / 1024 / 1024:.1f} MB"
            )
            continue

        if not thumbnail_url:
            print("No thumbnail URL.")
            continue

        print(f"Found: {title}")
        print(
            f"Size: {width}x{height}"
        )

        image_data = download_image(
            thumbnail_url
        )

        if image_data is None and original_url:
            print(
                "Trying original..."
            )

            image_data = download_image(
                original_url
            )

        if image_data is None:
            print(
                "Couldn't download image."
            )
            continue

        print("Converting to JPEG...")

        jpeg_data = convert_to_jpeg(
            image_data
        )

        if jpeg_data is None:
            continue

        try:
            OUTPUT_FILE.write_bytes(
                jpeg_data
            )

        except OSError as e:
            print(
                f"Couldn't save image: {e}"
            )
            continue

        metadata = {
            "title": title,
            "source": "Wikimedia Commons",
            "output_format": "JPEG",

            "width": width,
            "height": height,

            "original_mime": mime,

            "original_size": size,

            "jpeg_size": len(
                jpeg_data
            ),

            "original_url": (
                clean_url(original_url)
                if original_url
                else None
            ),

            "thumbnail_url": (
                clean_url(thumbnail_url)
                if thumbnail_url
                else None
            ),
        }

        try:
            METADATA_FILE.write_text(
                json.dumps(
                    metadata,
                    indent=4,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        except OSError as e:
            print(
                f"Warning: couldn't save "
                f"metadata: {e}"
            )

        print()
        print("=" * 50)
        print("SUCCESS")
        print("=" * 50)
        print(
            f"Image: "
            f"{OUTPUT_FILE.absolute()}"
        )
        print(
            f"Size: "
            f"{len(jpeg_data) / 1024:.1f} KB"
        )
        print(
            f"Source: {title}"
        )

        return metadata

    print("[WARNING] Could not download image from Wikimedia Commons. Generating local fallback image.")
    return generate_fallback_image()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    get_random_image()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    get_random_image()