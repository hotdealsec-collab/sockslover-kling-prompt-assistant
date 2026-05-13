import os
import re
import io
import csv
import json
import time
import zipfile
import hashlib
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

APP_TITLE = "SocksLover Kling Prompt Assistant"
DEFAULT_MODEL = "gpt-4.1-mini"
OUTPUT_DIR = "outputs"
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")
ZIP_DIR = os.path.join(OUTPUT_DIR, "zips")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "generation_log.csv")

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(ZIP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

CATEGORY_OPTIONS = [
    "Bag",
    "Socks",
    "Hat / Scarf",
    "Accessory",
    "Other",
]

STYLE_OPTIONS = [
    "Clean ecommerce",
    "Cute lifestyle",
    "Premium minimal",
    "Daily outing",
]

RATIO_OPTIONS = ["1:1", "4:5", "9:16", "16:9"]
DURATION_OPTIONS = ["5 seconds", "10 seconds"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_filename(text: str, max_len: int = 90) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣ぁ-んァ-ヶ一-龥._-]+", "-", text)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned[:max_len] or "product"


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


def extract_shopify_product_data(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.find("meta", property="og:title"):
        title = soup.find("meta", property="og:title").get("content", "")
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = normalize_space(title)

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    og_desc = soup.find("meta", property="og:description")
    if meta_desc:
        description = meta_desc.get("content", "")
    elif og_desc:
        description = og_desc.get("content", "")
    description = normalize_space(description)

    price = ""
    if soup.find("meta", property="product:price:amount"):
        price = soup.find("meta", property="product:price:amount").get("content", "")

    product_json = {}
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") in ["Product", ["Product"]]:
                product_json = item
                break
        if product_json:
            break

    if product_json:
        title = title or normalize_space(product_json.get("name", ""))
        description = description or normalize_space(product_json.get("description", ""))

    images = []

    # 1) JSON-LD images
    json_images = product_json.get("image", []) if product_json else []
    if isinstance(json_images, str):
        json_images = [json_images]
    for img_url in json_images:
        if isinstance(img_url, str):
            images.append({"url": urljoin(url, img_url), "alt": title, "source": "json-ld"})

    # 2) og image
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        images.append({"url": urljoin(url, og_image.get("content")), "alt": title, "source": "og:image"})

    # 3) img tags. This includes body/product-description images and product media images.
    for img in soup.find_all("img"):
        candidates = [
            img.get("src"),
            img.get("data-src"),
            img.get("data-original"),
            img.get("data-zoom"),
        ]
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            # Pick the largest-looking srcset candidate.
            srcset_urls = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
            candidates.extend(srcset_urls[-2:])
        for candidate in candidates:
            if not candidate:
                continue
            full_url = urljoin(url, candidate)
            alt = normalize_space(img.get("alt", ""))
            images.append({"url": full_url, "alt": alt, "source": "img-tag"})

    images = dedupe_and_filter_images(images)

    return {
        "url": url,
        "title": title,
        "description": description,
        "price": price,
        "images": images,
    }


def dedupe_and_filter_images(images: list[dict]) -> list[dict]:
    seen = set()
    cleaned = []
    blocked_patterns = [
        "icon",
        "logo",
        "sprite",
        "placeholder",
        "payment",
        "favicon",
        "no-image",
        "loading",
    ]

    for item in images:
        raw_url = item.get("url", "")
        if not raw_url or raw_url.startswith("data:"):
            continue
        parsed = urlparse(raw_url)
        if not parsed.scheme.startswith("http"):
            continue
        lowered = raw_url.lower()
        if any(p in lowered for p in blocked_patterns):
            continue
        if not any(ext in lowered for ext in [".jpg", ".jpeg", ".png", ".webp", "cdn.shopify.com", "alicdn", "ae01"]):
            continue

        # Normalize Shopify size suffixes lightly for dedupe, while preserving the original URL.
        dedupe_key = re.sub(r"_(\d+x\d+|\d+x|x\d+)\.(jpg|jpeg|png|webp)", r".\2", raw_url, flags=re.I)
        dedupe_key = dedupe_key.split("?")[0]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(item)

    return cleaned[:40]


def infer_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if any(k in text for k in ["bag", "バッグ", "鞄", "かばん", "ショルダー", "トート", "ポーチ"]):
        return "Bag"
    if any(k in text for k in ["sock", "socks", "靴下", "ソックス", "くつ下"]):
        return "Socks"
    if any(k in text for k in ["hat", "cap", "scarf", "帽子", "ハット", "キャップ", "スカーフ"]):
        return "Hat / Scarf"
    return "Other"


def build_prompt_request(product: dict, selected_images: list[dict], category: str, style: str, ratio: str, duration: str) -> str:
    image_notes = "\n".join(
        [f"- Image {i+1}: source={img.get('source','')}, alt={img.get('alt','')}, url={img.get('url','')}" for i, img in enumerate(selected_images)]
    )

    return f"""
You are a senior ecommerce creative director creating prompts for Kling AI image-to-video generation.
The store is SocksLover, a Japanese ecommerce store selling socks, bags, and small fashion/lifestyle items.

Create a practical Kling prompt for a short product video.
Important rule: The video must NOT include any text, captions, typography, letters, logos, watermarks, or subtitles.
The product must remain faithful to the reference images. Do not change product design, color, pattern, shape, material, strap, length, or texture.

Product information:
- Product URL: {product.get('url')}
- Product title: {product.get('title')}
- Product description: {product.get('description')}
- Category: {category}
- Desired style: {style}
- Desired aspect ratio: {ratio}
- Desired duration: {duration}

Selected reference images:
{image_notes}

Return the result in the following exact structure:

## Kling Main Prompt
Write one polished English prompt that can be copied directly into Kling.

## Negative Prompt
Write comma-separated negative keywords and phrases.

## Recommended Settings
- Duration:
- Aspect ratio:
- Motion:
- Style:

## Korean Notes
한국어로 짧게, 이 프롬프트의 의도와 Kling에서 주의할 점을 설명하세요.
""".strip()


def generate_prompt_with_openai(prompt_request: str, model: str, api_key: str) -> str:
    if not api_key:
        return fallback_prompt(prompt_request)
    if OpenAI is None:
        return "OpenAI 패키지를 불러오지 못했습니다. requirements.txt 설치를 확인해주세요."

    client = OpenAI(api_key=api_key.strip())
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You create safe, faithful, practical ecommerce video-generation prompts. Never add text overlays to the video prompt.",
            },
            {"role": "user", "content": prompt_request},
        ],
        temperature=0.35,
    )
    return response.choices[0].message.content.strip()


def fallback_prompt(prompt_request: str) -> str:
    # Simple offline fallback when OPENAI_API_KEY is not configured.
    return """## Kling Main Prompt
Create a clean and elegant ecommerce product video using the selected product images as reference. Keep the product design, color, silhouette, texture, material, pattern, and all details faithful to the reference images. Use subtle camera movement, soft natural lighting, and a simple minimal background. Focus on the product's shape, texture, and everyday appeal. The video should feel like a friendly Japanese online store product presentation. No text, no captions, no typography, no letters, no logos, no watermarks, no subtitles.

## Negative Prompt
wrong product shape, changed color, changed pattern, distorted product, deformed material, extra objects, unrelated accessories, messy background, blurry details, excessive motion, text, letters, captions, typography, logo, watermark, subtitle

## Recommended Settings
- Duration: 5 seconds
- Aspect ratio: 1:1
- Motion: subtle camera movement
- Style: clean ecommerce product video

## Korean Notes
OpenAI API Key가 입력되지 않아 기본 프롬프트를 표시했습니다. 상품 카테고리와 선택 이미지에 맞춰 세부 표현을 수동으로 조금 보정한 뒤 Kling에 입력해주세요.
"""


def download_image(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "image" not in content_type and len(r.content) < 1000:
            return None
        return r.content
    except Exception:
        return None


def create_zip(product_title: str, selected_images: list[dict], prompt_text: str) -> tuple[str, bytes]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(product_title)
    zip_name = f"{timestamp}_{base}_kling_assets.zip"
    zip_path = os.path.join(ZIP_DIR, zip_name)

    memory_zip = io.BytesIO()
    with zipfile.ZipFile(memory_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("kling_prompt.txt", prompt_text)
        zf.writestr("selected_images.csv", pd.DataFrame(selected_images).to_csv(index=False))
        for i, img in enumerate(selected_images, start=1):
            img_bytes = download_image(img["url"])
            if not img_bytes:
                continue
            ext = ".jpg"
            lowered = img["url"].lower().split("?")[0]
            for candidate_ext in [".webp", ".png", ".jpeg", ".jpg"]:
                if lowered.endswith(candidate_ext):
                    ext = candidate_ext
                    break
            zf.writestr(f"images/image_{i:02d}{ext}", img_bytes)

    memory_zip.seek(0)
    with open(zip_path, "wb") as f:
        f.write(memory_zip.getvalue())
    return zip_name, memory_zip.getvalue()


def append_log(row: dict) -> None:
    exists = os.path.exists(LOG_FILE)
    fieldnames = [
        "created_at",
        "product_url",
        "product_title",
        "category",
        "style",
        "ratio",
        "duration",
        "selected_image_count",
        "model",
        "prompt_hash",
        "video_created",
        "shopify_uploaded",
        "memo",
    ]
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


def render_image_grid(images: list[dict]) -> list[int]:
    selected_indices = []
    if not images:
        st.warning("추출된 이미지가 없습니다. 상품 페이지 HTML 구조를 확인해주세요.")
        return selected_indices

    cols_per_row = 4
    for row_start in range(0, len(images), cols_per_row):
        cols = st.columns(cols_per_row)
        for offset, col in enumerate(cols):
            idx = row_start + offset
            if idx >= len(images):
                continue
            img = images[idx]
            with col:
                st.image(img["url"], use_container_width=True)
                default_checked = idx < 4
                checked = st.checkbox(
                    f"사용 {idx + 1}",
                    value=default_checked,
                    key=f"image_check_{idx}",
                )
                st.caption(f"{img.get('source', '')} / {normalize_space(img.get('alt',''))[:45]}")
                if checked:
                    selected_indices.append(idx)
    return selected_indices


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🧦", layout="wide")
    st.title("🧦 SocksLover Kling Prompt Assistant")
    st.caption("Shopify 상품 URL에서 이미지를 추출하고, Kling용 영상 프롬프트를 생성하는 반자동 MVP v4.2")

    with st.sidebar:
        st.header("설정")
        model = st.text_input("OpenAI model", value=DEFAULT_MODEL)
        api_key = st.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="sk-...",
            help="이 키는 현재 Streamlit 세션에서만 사용되며, 로그/ZIP/CSV에 저장되지 않습니다.",
        )
        st.caption("API Key는 실행 중인 화면에서만 입력합니다. GitHub나 .env 파일에 저장하지 않습니다.")
        st.divider()
        st.markdown("### MVP 범위")
        st.write("✅ 이미지 추출")
        st.write("✅ GPT 프롬프트 생성")
        st.write("✅ ZIP 다운로드")
        st.write("✅ CSV 로그 저장")
        st.write("❌ Kling API 연동 없음")
        st.write("❌ Shopify 업로드 자동화 없음")

    product_url = st.text_input(
        "Shopify 상품 URL",
        placeholder="https://sockslover.net/products/xxxxx",
    )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        fetch_clicked = st.button("상품 정보 가져오기", type="primary", use_container_width=True)

    if fetch_clicked:
        if not product_url.strip():
            st.error("상품 URL을 입력해주세요.")
        else:
            with st.spinner("상품 페이지를 읽고 이미지를 추출하는 중입니다..."):
                try:
                    html = fetch_html(product_url.strip())
                    product = extract_shopify_product_data(product_url.strip(), html)
                    st.session_state["product"] = product
                except Exception as e:
                    st.error(f"상품 정보를 가져오지 못했습니다: {e}")

    product = st.session_state.get("product")
    if not product:
        st.info("상품 URL을 입력하고 [상품 정보 가져오기]를 눌러주세요.")
        return

    st.divider()
    st.subheader("1. 상품 정보")
    info_col1, info_col2, info_col3 = st.columns([2, 2, 1])
    with info_col1:
        st.write("**상품명**")
        st.write(product.get("title") or "-" )
    with info_col2:
        st.write("**설명 요약**")
        st.write((product.get("description") or "-")[:250])
    with info_col3:
        st.metric("추출 이미지", len(product.get("images", [])))

    inferred = infer_category(product.get("title", ""), product.get("description", ""))

    st.subheader("2. 사용할 이미지 선택")
    st.caption("처음 3~4장을 기본 선택합니다. Kling에 업로드할 이미지 기준으로 체크해주세요.")
    selected_indices = render_image_grid(product.get("images", []))
    selected_images = [product["images"][i] for i in selected_indices]

    st.subheader("3. 영상 프롬프트 조건")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        category = st.selectbox("상품 카테고리", CATEGORY_OPTIONS, index=CATEGORY_OPTIONS.index(inferred) if inferred in CATEGORY_OPTIONS else 0)
    with c2:
        style = st.selectbox("영상 스타일", STYLE_OPTIONS, index=0)
    with c3:
        ratio = st.selectbox("추천 비율", RATIO_OPTIONS, index=0)
    with c4:
        duration = st.selectbox("영상 길이", DURATION_OPTIONS, index=0)

    st.caption("MVP v4.2 원칙: 영상 안에 텍스트/자막/로고를 넣지 않습니다.")

    generate_clicked = st.button("Kling용 프롬프트 생성", type="primary", use_container_width=True)

    if generate_clicked:
        if not selected_images:
            st.error("최소 1장 이상의 이미지를 선택해주세요.")
        else:
            with st.spinner("GPT가 Kling용 프롬프트를 생성하는 중입니다..."):
                prompt_request = build_prompt_request(product, selected_images, category, style, ratio, duration)
                try:
                    result = generate_prompt_with_openai(prompt_request, model, api_key)
                except Exception as e:
                    result = f"프롬프트 생성 실패: {e}\n\n" + fallback_prompt(prompt_request)
                st.session_state["generated_prompt"] = result
                st.session_state["selected_images"] = selected_images
                st.session_state["prompt_meta"] = {
                    "category": category,
                    "style": style,
                    "ratio": ratio,
                    "duration": duration,
                    "model": model,
                }

    generated = st.session_state.get("generated_prompt")
    if generated:
        st.divider()
        st.subheader("4. 생성된 Kling 프롬프트")
        st.text_area("복사해서 Kling에 붙여넣기", value=generated, height=420)

        d1, d2, d3 = st.columns(3)
        with d1:
            if st.button("작업 로그 저장", use_container_width=True):
                meta = st.session_state.get("prompt_meta", {})
                append_log({
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "product_url": product.get("url"),
                    "product_title": product.get("title"),
                    "category": meta.get("category"),
                    "style": meta.get("style"),
                    "ratio": meta.get("ratio"),
                    "duration": meta.get("duration"),
                    "selected_image_count": len(st.session_state.get("selected_images", [])),
                    "model": meta.get("model"),
                    "prompt_hash": hashlib.md5(generated.encode("utf-8")).hexdigest(),
                    "video_created": "",
                    "shopify_uploaded": "",
                    "memo": "",
                })
                st.success(f"로그 저장 완료: {LOG_FILE}")
        with d2:
            zip_name, zip_bytes = create_zip(product.get("title", "product"), st.session_state.get("selected_images", []), generated)
            st.download_button(
                "선택 이미지 + 프롬프트 ZIP 다운로드",
                data=zip_bytes,
                file_name=zip_name,
                mime="application/zip",
                use_container_width=True,
            )
        with d3:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "rb") as f:
                    st.download_button(
                        "CSV 로그 다운로드",
                        data=f.read(),
                        file_name="generation_log.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

        st.subheader("5. 수동 운영 체크리스트")
        st.markdown(
            """
- ZIP 안의 선택 이미지를 Kling에 업로드합니다.
- 생성된 Main Prompt / Negative Prompt를 Kling에 붙여넣습니다.
- 영상 안에 텍스트, 로고, 가짜 문자가 생기면 재생성합니다.
- 상품 디자인, 색상, 패턴, 스트랩, 소재감이 실제 상품과 다르면 사용하지 않습니다.
- 최종 MP4를 다운로드한 뒤 Shopify 상품 미디어에 수동 등록합니다.
            """.strip()
        )


if __name__ == "__main__":
    main()
