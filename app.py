import os
import re
import io
import csv
import json
import zipfile
import hashlib
from datetime import datetime
from itertools import combinations
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
APP_VERSION = "MVP v4.3"
DEFAULT_MODEL = "gpt-4.1-mini"
OUTPUT_DIR = "outputs"
ZIP_DIR = os.path.join(OUTPUT_DIR, "zips")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "generation_log.csv")

os.makedirs(ZIP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

CATEGORY_OPTIONS = ["Bag", "Socks", "Hat / Scarf", "Accessory", "Other"]
STYLE_OPTIONS = ["Clean ecommerce", "Cute lifestyle", "Premium minimal", "Daily outing"]
RATIO_OPTIONS = ["1:1", "4:5", "9:16", "16:9"]
DURATION_OPTIONS = ["5 seconds", "10 seconds"]
IMAGE_TYPE_LABELS = {
    "single_product": "단일 상품컷",
    "model_shot": "모델/착용컷",
    "multi_product": "여러 상품컷",
    "collage": "콜라주",
    "detail_shot": "디테일컷",
    "lifestyle": "라이프스타일컷",
    "unclear": "불명확",
}

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
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣ぁ-んァ-ヶ一-龥._-]+", "-", text or "")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned[:max_len] or "product"


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


def extract_shopify_product_data(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", "")
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
    price_meta = soup.find("meta", property="product:price:amount")
    if price_meta:
        price = price_meta.get("content", "")

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
            if isinstance(item, dict):
                item_type = item.get("@type")
                if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
                    product_json = item
                    break
        if product_json:
            break

    if product_json:
        title = title or normalize_space(product_json.get("name", ""))
        description = description or normalize_space(product_json.get("description", ""))

    images = []

    json_images = product_json.get("image", []) if product_json else []
    if isinstance(json_images, str):
        json_images = [json_images]
    for img_url in json_images:
        if isinstance(img_url, str):
            images.append({"url": urljoin(url, img_url), "alt": title, "source": "json-ld"})

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        images.append({"url": urljoin(url, og_image.get("content")), "alt": title, "source": "og:image"})

    for img in soup.find_all("img"):
        candidates = [img.get("src"), img.get("data-src"), img.get("data-original"), img.get("data-zoom")]
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            srcset_urls = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
            candidates.extend(srcset_urls[-2:])
        for candidate in candidates:
            if not candidate:
                continue
            full_url = urljoin(url, candidate)
            alt = normalize_space(img.get("alt", ""))
            images.append({"url": full_url, "alt": alt, "source": "img-tag"})

    images = dedupe_and_filter_images(images)
    for i, img in enumerate(images, start=1):
        img["image_id"] = str(i)
        img["label"] = f"사용 {i}"

    return {"url": url, "title": title, "description": description, "price": price, "images": images}


def dedupe_and_filter_images(images: list[dict]) -> list[dict]:
    seen = set()
    cleaned = []
    blocked_patterns = ["icon", "logo", "sprite", "placeholder", "payment", "favicon", "no-image", "loading"]

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


def json_from_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def build_vision_messages(product: dict, images_for_analysis: list[dict], category: str) -> list[dict]:
    instructions = f"""
You are an ecommerce creative director and image selector for Kling AI image-to-video generation.
The store is SocksLover, a Japanese Shopify store.
Kling will use exactly 2 images: one start frame and one end frame.
Your job is to analyze product images and recommend the best image pair.

Product:
- title: {product.get('title')}
- description: {product.get('description')}
- category: {category}

Selection principles:
- Prefer clean single-product images.
- Prefer two images that look like the same product, same color, similar background, and natural visual continuity.
- Avoid collage images, multi-product group images, heavy model/body-focused images, and images with too many unrelated objects.
- Avoid product-only image + very different model shot if it creates an unnatural transition.
- For bags, prefer full product shot -> slightly different angle or slightly closer product shot.
- For socks, prefer product shot -> similar product/detail shot, or model/fit shots only when both images share a similar pose and background.
- The final video must NOT contain text, captions, typography, logos, watermarks, or fake letters.

Return JSON only. Do not include markdown.
Use this schema:
{{
  "images": [
    {{
      "image_id": "1",
      "image_type": "single_product | model_shot | multi_product | collage | detail_shot | lifestyle | unclear",
      "suitability_score": 0,
      "recommended_role": "start_frame | end_frame | either | not_recommended",
      "short_reason_ko": "Korean short reason",
      "cautions_ko": "Korean caution or empty string"
    }}
  ],
  "pair_recommendations": [
    {{
      "rank": 1,
      "start_image_id": "1",
      "end_image_id": "2",
      "pair_score": 0,
      "reason_ko": "Korean reason",
      "cautions_ko": "Korean caution",
      "suggested_motion": "subtle zoom in / slow pan / gentle camera move"
    }}
  ],
  "overall_notes_ko": "Short Korean advice for the operator"
}}
Return up to 3 pair recommendations. If no good pair exists, return the least risky pair and explain the caution.
""".strip()

    content = [{"type": "text", "text": instructions}]
    for img in images_for_analysis:
        content.append({"type": "text", "text": f"Image ID: {img['image_id']} / label: {img.get('label','')} / source: {img.get('source','')} / alt: {img.get('alt','')}"})
        content.append({"type": "image_url", "image_url": {"url": img["url"], "detail": "low"}})
    return [{"role": "user", "content": content}]


def analyze_images_and_pairs(product: dict, images_for_analysis: list[dict], category: str, model: str, api_key: str) -> dict:
    if not api_key:
        raise ValueError("OpenAI API Key를 입력해야 GPT Vision 분석을 사용할 수 있습니다.")
    if OpenAI is None:
        raise RuntimeError("OpenAI 패키지를 불러오지 못했습니다. requirements.txt 설치를 확인해주세요.")

    client = OpenAI(api_key=api_key.strip())
    response = client.chat.completions.create(
        model=model,
        messages=build_vision_messages(product, images_for_analysis, category),
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json_from_text(response.choices[0].message.content)


def get_analysis_for_image(analysis: dict, image_id: str) -> dict:
    for item in analysis.get("images", []):
        if str(item.get("image_id")) == str(image_id):
            return item
    return {}


def build_prompt_request(
    product: dict,
    selected_images: list[dict],
    category: str,
    style: str,
    ratio: str,
    duration: str,
    selected_pair: dict | None,
    image_analysis: dict | None,
) -> str:
    image_notes = []
    for img in selected_images:
        analysis_item = get_analysis_for_image(image_analysis or {}, img.get("image_id"))
        image_notes.append(
            f"- Image {img.get('image_id')}: source={img.get('source','')}, alt={img.get('alt','')}, "
            f"type={analysis_item.get('image_type','')}, score={analysis_item.get('suitability_score','')}, url={img.get('url','')}"
        )

    pair_notes = ""
    if selected_pair:
        pair_notes = f"""
Selected pair recommendation:
- Start image ID: {selected_pair.get('start_image_id')}
- End image ID: {selected_pair.get('end_image_id')}
- Pair score: {selected_pair.get('pair_score')}
- Reason: {selected_pair.get('reason_ko')}
- Suggested motion: {selected_pair.get('suggested_motion')}
- Cautions: {selected_pair.get('cautions_ko')}
""".strip()

    return f"""
You are a senior ecommerce creative director creating prompts for Kling AI image-to-video generation.
The store is SocksLover, a Japanese ecommerce store selling socks, bags, and small fashion/lifestyle items.

Create a practical Kling prompt for a short product video using exactly the selected start/end reference images.
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
{chr(10).join(image_notes)}

{pair_notes}

Return the result in the following exact structure:

## Kling Main Prompt
Write one polished English prompt that can be copied directly into Kling. Mention start frame and end frame naturally. Keep motion subtle.

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
        return fallback_prompt()
    if OpenAI is None:
        return "OpenAI 패키지를 불러오지 못했습니다. requirements.txt 설치를 확인해주세요."

    client = OpenAI(api_key=api_key.strip())
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You create safe, faithful, practical ecommerce video-generation prompts. Never add text overlays to the video prompt."},
            {"role": "user", "content": prompt_request},
        ],
        temperature=0.35,
    )
    return response.choices[0].message.content.strip()


def fallback_prompt() -> str:
    return """## Kling Main Prompt
Create a clean and elegant ecommerce product video using the selected start and end product images as reference. Keep the product design, color, silhouette, texture, material, pattern, and all details faithful to the reference images. Use subtle camera movement, soft natural lighting, and a simple minimal background. Focus on the product's shape, texture, and everyday appeal. The video should feel like a friendly Japanese online store product presentation. No text, no captions, no typography, no letters, no logos, no watermarks, no subtitles.

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


def create_zip(product_title: str, selected_images: list[dict], prompt_text: str, analysis: dict | None, selected_pair: dict | None) -> tuple[str, bytes]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(product_title)
    zip_name = f"{timestamp}_{base}_kling_assets.zip"
    zip_path = os.path.join(ZIP_DIR, zip_name)

    memory_zip = io.BytesIO()
    with zipfile.ZipFile(memory_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("kling_prompt.txt", prompt_text)
        zf.writestr("selected_images.csv", pd.DataFrame(selected_images).to_csv(index=False))
        if analysis:
            zf.writestr("vision_analysis.json", json.dumps(analysis, ensure_ascii=False, indent=2))
        if selected_pair:
            zf.writestr("selected_pair.json", json.dumps(selected_pair, ensure_ascii=False, indent=2))
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
            zf.writestr(f"images/image_{i:02d}_source_{img.get('image_id')}{ext}", img_bytes)

    memory_zip.seek(0)
    with open(zip_path, "wb") as f:
        f.write(memory_zip.getvalue())
    return zip_name, memory_zip.getvalue()


def append_log(row: dict) -> None:
    exists = os.path.exists(LOG_FILE)
    fieldnames = [
        "created_at", "product_url", "product_title", "category", "style", "ratio", "duration",
        "selected_image_ids", "selected_pair_score", "model", "prompt_hash", "video_created",
        "shopify_uploaded", "memo",
    ]
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


def render_image_cards(images: list[dict], analysis: dict | None = None) -> None:
    if not images:
        st.warning("추출된 이미지가 없습니다. 상품 페이지 HTML 구조를 확인해주세요.")
        return
    cols_per_row = 4
    for row_start in range(0, len(images), cols_per_row):
        cols = st.columns(cols_per_row)
        for offset, col in enumerate(cols):
            idx = row_start + offset
            if idx >= len(images):
                continue
            img = images[idx]
            a = get_analysis_for_image(analysis or {}, img.get("image_id"))
            with col:
                st.image(img["url"], use_container_width=True)
                st.markdown(f"**사용 {img.get('image_id')}**")
                if a:
                    score = int(a.get("suitability_score") or 0)
                    img_type = a.get("image_type", "unclear")
                    st.caption(f"유형: {IMAGE_TYPE_LABELS.get(img_type, img_type)} / 적합도: {score}점")
                    st.caption(f"역할: {a.get('recommended_role', '-')}")
                    st.caption(a.get("short_reason_ko", ""))
                    if a.get("cautions_ko"):
                        st.warning(a.get("cautions_ko"), icon="⚠️")
                else:
                    st.caption(f"{img.get('source', '')} / {normalize_space(img.get('alt',''))[:45]}")


def pair_label(pair: dict) -> str:
    return f"추천 {pair.get('rank')} | 시작 {pair.get('start_image_id')} → 끝 {pair.get('end_image_id')} | {pair.get('pair_score')}점"


def selected_images_from_pair(images: list[dict], pair: dict | None) -> list[dict]:
    if not pair:
        return []
    ids = [str(pair.get("start_image_id")), str(pair.get("end_image_id"))]
    selected = []
    for img_id in ids:
        for img in images:
            if str(img.get("image_id")) == img_id:
                selected.append(img)
                break
    return selected


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🧦", layout="wide")
    st.title("🧦 SocksLover Kling Prompt Assistant")
    st.caption("Shopify 상품 URL에서 이미지를 추출하고, GPT Vision으로 Kling용 시작/끝 이미지 pair를 추천하는 반자동 MVP v4.3")

    with st.sidebar:
        st.header("설정")
        model = st.text_input("OpenAI model", value=DEFAULT_MODEL)
        api_key = st.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="sk-...",
            help="이 키는 현재 Streamlit 세션에서만 사용되며, 로그/ZIP/CSV에 저장되지 않습니다.",
        )
        max_vision_images = st.slider("Vision 분석 최대 이미지 수", min_value=4, max_value=20, value=12, step=1)
        st.caption("이미지가 많을수록 Vision 분석 비용과 시간이 늘어납니다. 처음에는 8~12장 추천.")
        st.divider()
        st.markdown("### MVP 범위")
        st.write("✅ 이미지 자동 추출")
        st.write("✅ 이미지 카드 표시")
        st.write("✅ GPT Vision 이미지 유형/적합도 분석")
        st.write("✅ Pair 추천 자동화")
        st.write("✅ Kling 프롬프트 생성")
        st.write("❌ Kling API 연동 없음")
        st.write("❌ Shopify 업로드 자동화 없음")

    product_url = st.text_input("Shopify 상품 URL", placeholder="https://sockslover.net/products/xxxxx")
    fetch_clicked = st.button("상품 정보 가져오기", type="primary")

    if fetch_clicked:
        if not product_url.strip():
            st.error("상품 URL을 입력해주세요.")
        else:
            with st.spinner("상품 페이지를 읽고 이미지를 추출하는 중입니다..."):
                try:
                    html = fetch_html(product_url.strip())
                    product = extract_shopify_product_data(product_url.strip(), html)
                    st.session_state["product"] = product
                    st.session_state.pop("vision_analysis", None)
                    st.session_state.pop("generated_prompt", None)
                    st.session_state.pop("selected_pair", None)
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
        st.write(product.get("title") or "-")
    with info_col2:
        st.write("**설명 요약**")
        st.write((product.get("description") or "-")[:250])
    with info_col3:
        st.metric("추출 이미지", len(product.get("images", [])))

    inferred = infer_category(product.get("title", ""), product.get("description", ""))
    category = st.selectbox("상품 카테고리", CATEGORY_OPTIONS, index=CATEGORY_OPTIONS.index(inferred) if inferred in CATEGORY_OPTIONS else 0)

    st.subheader("2. 이미지 카드")
    st.caption("GPT Vision 분석 전에는 단순 이미지 목록만 표시됩니다. 분석 후에는 유형/적합도/추천 사유가 카드에 표시됩니다.")
    render_image_cards(product.get("images", []), st.session_state.get("vision_analysis"))

    st.subheader("3. GPT Vision 이미지 분석 & Pair 추천")
    st.caption("Kling에서 시작/끝 프레임으로 쓰기 좋은 2장 조합을 추천합니다. 콜라주/여러상품/착샷 혼합 위험을 자동으로 평가합니다.")
    analyze_clicked = st.button("GPT Vision으로 분석하고 Pair 추천", type="primary", use_container_width=True)

    if analyze_clicked:
        if not api_key.strip():
            st.error("OpenAI API Key를 입력해주세요.")
        else:
            images_for_analysis = product.get("images", [])[:max_vision_images]
            if len(images_for_analysis) < 2:
                st.error("Pair 추천에는 최소 2장의 이미지가 필요합니다.")
            else:
                with st.spinner("GPT Vision이 이미지를 분석하고 추천 조합을 만드는 중입니다..."):
                    try:
                        analysis = analyze_images_and_pairs(product, images_for_analysis, category, model, api_key)
                        st.session_state["vision_analysis"] = analysis
                        st.success("분석 완료. 아래 추천 조합을 확인해주세요.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Vision 분석 실패: {e}")

    analysis = st.session_state.get("vision_analysis")
    selected_pair = None
    selected_images = []

    if analysis:
        st.subheader("4. 추천 Pair TOP 3")
        pairs = analysis.get("pair_recommendations", [])
        if pairs:
            for pair in pairs:
                with st.container(border=True):
                    st.markdown(f"**{pair_label(pair)}**")
                    pcols = st.columns([1, 1, 3])
                    imgs = selected_images_from_pair(product.get("images", []), pair)
                    with pcols[0]:
                        if len(imgs) > 0:
                            st.image(imgs[0]["url"], caption=f"Start: 사용 {pair.get('start_image_id')}", use_container_width=True)
                    with pcols[1]:
                        if len(imgs) > 1:
                            st.image(imgs[1]["url"], caption=f"End: 사용 {pair.get('end_image_id')}", use_container_width=True)
                    with pcols[2]:
                        st.write(pair.get("reason_ko", ""))
                        if pair.get("cautions_ko"):
                            st.warning(pair.get("cautions_ko"), icon="⚠️")
                        st.caption(f"추천 모션: {pair.get('suggested_motion', '-')}")

            pair_options = {pair_label(p): p for p in pairs}
            chosen_label = st.radio("사용할 추천 조합 선택", list(pair_options.keys()), index=0)
            selected_pair = pair_options[chosen_label]
            st.session_state["selected_pair"] = selected_pair
            selected_images = selected_images_from_pair(product.get("images", []), selected_pair)
        else:
            st.warning("추천 pair가 없습니다. 이미지 2장을 수동으로 선택해주세요.")

        if analysis.get("overall_notes_ko"):
            st.info(analysis.get("overall_notes_ko"))

    st.subheader("5. Kling 프롬프트 조건")
    c1, c2, c3 = st.columns(3)
    with c1:
        style = st.selectbox("영상 스타일", STYLE_OPTIONS, index=0)
    with c2:
        ratio = st.selectbox("추천 비율", RATIO_OPTIONS, index=0)
    with c3:
        duration = st.selectbox("영상 길이", DURATION_OPTIONS, index=0)

    st.caption("원칙: 영상 안에 텍스트/자막/로고를 넣지 않습니다. Kling에는 추천된 2장만 업로드하는 것을 권장합니다.")

    if selected_images:
        st.markdown("**현재 선택된 시작/끝 이미지**")
        s1, s2 = st.columns(2)
        with s1:
            st.image(selected_images[0]["url"], caption=f"Start: 사용 {selected_images[0].get('image_id')}", use_container_width=True)
        with s2:
            if len(selected_images) > 1:
                st.image(selected_images[1]["url"], caption=f"End: 사용 {selected_images[1].get('image_id')}", use_container_width=True)

    generate_clicked = st.button("선택 Pair 기반 Kling용 프롬프트 생성", type="primary", use_container_width=True)

    if generate_clicked:
        if not selected_images or len(selected_images) < 2:
            st.error("먼저 추천 Pair를 선택해주세요. Kling 시작/끝 프레임에는 2장이 필요합니다.")
        else:
            with st.spinner("GPT가 Kling용 프롬프트를 생성하는 중입니다..."):
                prompt_request = build_prompt_request(product, selected_images, category, style, ratio, duration, selected_pair, analysis)
                try:
                    result = generate_prompt_with_openai(prompt_request, model, api_key)
                except Exception as e:
                    result = f"프롬프트 생성 실패: {e}\n\n" + fallback_prompt()
                st.session_state["generated_prompt"] = result
                st.session_state["selected_images"] = selected_images
                st.session_state["prompt_meta"] = {"category": category, "style": style, "ratio": ratio, "duration": duration, "model": model}

    generated = st.session_state.get("generated_prompt")
    if generated:
        st.divider()
        st.subheader("6. 생성된 Kling 프롬프트")
        st.text_area("복사해서 Kling에 붙여넣기", value=generated, height=420)

        d1, d2, d3 = st.columns(3)
        with d1:
            if st.button("작업 로그 저장", use_container_width=True):
                meta = st.session_state.get("prompt_meta", {})
                current_pair = st.session_state.get("selected_pair", {})
                append_log({
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "product_url": product.get("url"),
                    "product_title": product.get("title"),
                    "category": meta.get("category"),
                    "style": meta.get("style"),
                    "ratio": meta.get("ratio"),
                    "duration": meta.get("duration"),
                    "selected_image_ids": ",".join([str(img.get("image_id")) for img in st.session_state.get("selected_images", [])]),
                    "selected_pair_score": current_pair.get("pair_score", ""),
                    "model": meta.get("model"),
                    "prompt_hash": hashlib.md5(generated.encode("utf-8")).hexdigest(),
                    "video_created": "",
                    "shopify_uploaded": "",
                    "memo": "",
                })
                st.success(f"로그 저장 완료: {LOG_FILE}")
        with d2:
            zip_name, zip_bytes = create_zip(
                product.get("title", "product"),
                st.session_state.get("selected_images", []),
                generated,
                st.session_state.get("vision_analysis"),
                st.session_state.get("selected_pair"),
            )
            st.download_button("선택 이미지 + 프롬프트 ZIP 다운로드", data=zip_bytes, file_name=zip_name, mime="application/zip", use_container_width=True)
        with d3:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "rb") as f:
                    st.download_button("CSV 로그 다운로드", data=f.read(), file_name="generation_log.csv", mime="text/csv", use_container_width=True)

        st.subheader("7. 수동 운영 체크리스트")
        st.markdown(
            """
- ZIP 안의 시작/끝 이미지 2장을 Kling에 업로드합니다.
- 생성된 Main Prompt / Negative Prompt를 Kling에 붙여넣습니다.
- 영상 안에 텍스트, 로고, 가짜 문자가 생기면 재생성합니다.
- 상품 디자인, 색상, 패턴, 스트랩, 소재감이 실제 상품과 다르면 사용하지 않습니다.
- 최종 MP4를 다운로드한 뒤 Shopify 상품 미디어에 수동 등록합니다.
            """.strip()
        )


if __name__ == "__main__":
    main()
