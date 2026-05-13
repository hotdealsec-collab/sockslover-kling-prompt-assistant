import os
import re
import io
import csv
import json
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

APP_TITLE = "SocksLover Mini ShotFlow"
APP_VERSION = "MVP v4.5 Lifestyle First"
DEFAULT_MODEL = "gpt-4.1-mini"
OUTPUT_DIR = "outputs"
ZIP_DIR = os.path.join(OUTPUT_DIR, "zips")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "generation_log.csv")
TAKE_LOG_FILE = os.path.join(LOG_DIR, "take_log.csv")

os.makedirs(ZIP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

CATEGORY_OPTIONS = ["Bag", "Socks", "Hat / Scarf", "Accessory", "Other"]
STYLE_OPTIONS = ["Clean ecommerce", "Cute lifestyle", "Premium minimal", "Daily outing"]
VIDEO_STRATEGY_OPTIONS = {
    "Lifestyle First / 착샷 우선": {
        "summary": "모델 착샷을 우선 활용해 살아 있는 사용감 영상을 만듭니다.",
        "priority": "model_shot + model_shot > model_shot + single_product > single_product + single_product",
    },
    "Balanced / 착샷 + 제품 균형": {
        "summary": "착샷과 제품컷을 균형 있게 사용해 제품 인지와 사용 맥락을 함께 보여줍니다.",
        "priority": "model_shot + single_product > model_shot + model_shot > single_product + single_product",
    },
    "Product Focus / 제품컷 중심": {
        "summary": "상품 형태 보존을 최우선으로 하며 단독 제품컷을 우선합니다.",
        "priority": "single_product + single_product > model_shot + single_product > model_shot + model_shot",
    },
}
VIDEO_TYPE_OPTIONS = {
    "Shopify Product Video": {
        "ratio": "1:1",
        "duration": "5 seconds",
        "goal": "상품페이지에서 제품 형태와 소재감을 안정적으로 보여주기",
        "tone": "제품 충실도 최우선, 움직임은 절제",
    },
    "SNS Short Video": {
        "ratio": "4:5",
        "duration": "10 seconds",
        "goal": "Instagram/X/Pinterest용 짧은 감성 소재 만들기",
        "tone": "조금 더 감성적이지만 상품 변형 금지",
    },
    "Ad Creative Test": {
        "ratio": "1:1",
        "duration": "5 seconds",
        "goal": "광고 첫 노출에서 제품 매력을 빠르게 전달",
        "tone": "초반 시선 집중, 단 과한 연출 금지",
    },
    "Brand Mood Clip": {
        "ratio": "16:9",
        "duration": "10 seconds",
        "goal": "카테고리/브랜드 무드를 보여주는 짧은 분위기 영상",
        "tone": "브랜드 세계관 중심, 상품 일관성 유지",
    },
}
RATIO_OPTIONS = ["1:1", "4:5", "9:16", "16:9"]
DURATION_OPTIONS = ["5 seconds", "10 seconds"]
TAKE_STATUS_OPTIONS = ["Prompt Ready", "Submitted to Kling", "Generated", "Accepted", "Rejected", "Uploaded to Shopify"]
ISSUE_OPTIONS = [
    "상품 색상이 바뀜",
    "상품 형태가 무너짐",
    "가방 스트랩/손잡이가 이상함",
    "양말 패턴/길이가 바뀜",
    "모델/손/발이 어색함",
    "배경이 복잡함",
    "움직임이 과함",
    "제품이 너무 작음",
    "텍스트/로고/가짜 문자가 생김",
    "흐림/디테일 부족",
    "기타",
]
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


def video_type_settings(video_type: str) -> dict:
    return VIDEO_TYPE_OPTIONS.get(video_type, VIDEO_TYPE_OPTIONS["Shopify Product Video"])


def product_lock_rules(category: str) -> list[str]:
    base = [
        "Do not add any text, captions, typography, letters, logos, watermarks, or subtitles.",
        "Keep the original product design, color, silhouette, texture, material, and proportions faithful to the reference images.",
        "Use subtle, realistic motion only; avoid dramatic transformation or fantasy effects.",
        "Keep the background clean and avoid unrelated accessories or objects.",
        "If a model appears, keep the body, pose, outfit, and product placement natural and realistic.",
        "The model is only a context; the product must remain the hero and clearly visible.",
    ]
    if category == "Bag":
        base += [
            "Do not change the bag strap, handle, buckle, zipper, stitching, pocket position, or overall structure.",
            "Do not add extra straps, extra handles, charms, logos, or hardware that are not visible in the reference images.",
        ]
    elif category == "Socks":
        base += [
            "Do not change the sock pattern, color, length, fabric texture, ribbing, transparency, or pair structure.",
            "Avoid unrealistic feet, distorted legs, or fabric deformation.",
        ]
    elif category == "Hat / Scarf":
        base += [
            "Do not change the shape, brim, weave, fabric texture, pattern, or drape of the item.",
        ]
    return base


def build_scene_card(product: dict, category: str, video_type: str, video_strategy: str, style: str, ratio: str, duration: str, selected_pair: dict | None, selected_images: list[dict]) -> dict:
    settings = video_type_settings(video_type)
    start_id = selected_pair.get("start_image_id") if selected_pair else (selected_images[0].get("image_id") if selected_images else "")
    end_id = selected_pair.get("end_image_id") if selected_pair else (selected_images[1].get("image_id") if len(selected_images) > 1 else "")
    return {
        "product_title": product.get("title", ""),
        "product_url": product.get("url", ""),
        "category": category,
        "video_type": video_type,
        "video_strategy": video_strategy,
        "strategy_summary": VIDEO_STRATEGY_OPTIONS.get(video_strategy, {}).get("summary", ""),
        "goal": settings.get("goal", ""),
        "style": style,
        "ratio": ratio,
        "duration": duration,
        "start_image_id": start_id,
        "end_image_id": end_id,
        "camera_motion": selected_pair.get("suggested_motion", "subtle camera movement") if selected_pair else "subtle camera movement",
        "lighting": "soft natural light",
        "background": "clean minimal ecommerce background",
        "product_lock": product_lock_rules(category),
        "cautions": selected_pair.get("cautions_ko", "") if selected_pair else "",
    }


def scene_card_markdown(scene_card: dict) -> str:
    locks = "\n".join([f"- {rule}" for rule in scene_card.get("product_lock", [])])
    return f"""### Scene Card

**Product:** {scene_card.get('product_title')}  
**Video Type:** {scene_card.get('video_type')}  
**Video Strategy:** {scene_card.get('video_strategy')}  
**Strategy Summary:** {scene_card.get('strategy_summary')}  
**Goal:** {scene_card.get('goal')}  
**Category:** {scene_card.get('category')}  
**Start Image:** {scene_card.get('start_image_id')}  
**End Image:** {scene_card.get('end_image_id')}  
**Camera Motion:** {scene_card.get('camera_motion')}  
**Lighting:** {scene_card.get('lighting')}  
**Background:** {scene_card.get('background')}  
**Ratio / Duration:** {scene_card.get('ratio')} / {scene_card.get('duration')}  

**Product Lock**
{locks}

**Cautions:** {scene_card.get('cautions') or '-'}
""".strip()


def build_vision_messages(product: dict, images_for_analysis: list[dict], category: str, video_type: str, video_strategy: str) -> list[dict]:
    settings = video_type_settings(video_type)
    instructions = f"""
You are an ecommerce creative director and image selector for Kling AI image-to-video generation.
The store is SocksLover, a Japanese Shopify store.
Kling will use exactly 2 images: one start frame and one end frame.
Your job is to analyze product images and recommend the best image pair for the selected video type.

Product:
- title: {product.get('title')}
- description: {product.get('description')}
- category: {category}
- video_type: {video_type}
- video_strategy: {video_strategy}
- strategy_summary: {VIDEO_STRATEGY_OPTIONS.get(video_strategy, {}).get('summary', '')}
- strategy_pair_priority: {VIDEO_STRATEGY_OPTIONS.get(video_strategy, {}).get('priority', '')}
- video_goal: {settings.get('goal')}
- video_tone: {settings.get('tone')}

Selection principles:
- This version is Lifestyle First by default: for bags, hats, scarves, accessories, and fashion goods, model/lifestyle wearing shots are valuable because they create motion and usage context.
- If video_strategy is Lifestyle First, prioritize model_shot + model_shot when they show the same product/color and similar background or natural continuity.
- If video_strategy is Balanced, prioritize model_shot + single_product or single_product + model_shot when the product is clearly recognizable and transition is not too abrupt.
- If video_strategy is Product Focus, prioritize single_product + single_product, but still allow model_shot when product-only pairs are too static.
- Prefer model/lifestyle shots where the product is clearly visible and large enough, especially bags worn on shoulder/back or held naturally.
- Avoid face/body-dominant shots where the product is too small or unclear.
- Avoid collage images and multi-product group images as primary start/end frames unless no alternative exists.
- Avoid using the exact same image as both start and end unless explicitly unavoidable; it usually creates static videos.
- For bags, good lifestyle pairs include back-worn shot -> similar angle wearing shot, or product shot -> wearing shot when same color/product is obvious.
- For socks, use wearing/fit shots when both images have similar pose/background; otherwise use product/detail pair.
- The final video must NOT contain text, captions, typography, logos, watermarks, or fake letters.
- Return practical recommendations even if quality is imperfect, but label risks clearly. Do not stop at “no recommendation” unless all images are unusable.

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
      "suggested_motion": "visible but natural camera movement / gentle lifestyle motion / slight parallax"
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


def analyze_images_and_pairs(product: dict, images_for_analysis: list[dict], category: str, video_type: str, video_strategy: str, model: str, api_key: str) -> dict:
    if not api_key:
        raise ValueError("OpenAI API Key를 입력해야 GPT Vision 분석을 사용할 수 있습니다.")
    if OpenAI is None:
        raise RuntimeError("OpenAI 패키지를 불러오지 못했습니다. requirements.txt 설치를 확인해주세요.")

    client = OpenAI(api_key=api_key.strip())
    response = client.chat.completions.create(
        model=model,
        messages=build_vision_messages(product, images_for_analysis, category, video_type, video_strategy),
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json_from_text(response.choices[0].message.content)


def get_analysis_for_image(analysis: dict, image_id: str) -> dict:
    for item in analysis.get("images", []):
        if str(item.get("image_id")) == str(image_id):
            return item
    return {}


def build_prompt_request(product: dict, selected_images: list[dict], category: str, video_type: str, video_strategy: str, style: str, ratio: str, duration: str, selected_pair: dict | None, image_analysis: dict | None, scene_card: dict | None) -> str:
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
- Video type: {video_type}
- Video strategy: {video_strategy}
- Strategy summary: {VIDEO_STRATEGY_OPTIONS.get(video_strategy, {}).get('summary', '')}
- Video goal: {video_type_settings(video_type).get('goal')}
- Desired style: {style}
- Desired aspect ratio: {ratio}
- Desired duration: {duration}

Selected reference images:
{chr(10).join(image_notes)}

Product Lock rules that must be included in the prompt:
{chr(10).join(['- ' + r for r in product_lock_rules(category)])}

Scene Card:
{json.dumps(scene_card or {}, ensure_ascii=False, indent=2)}

{pair_notes}

Return the result in the following exact structure:

## Kling Main Prompt
Write one polished English prompt that can be copied directly into Kling. Mention start frame and end frame naturally. If the selected images include model/lifestyle shots, emphasize realistic lifestyle movement, natural body motion, slight parallax, and keeping the product as the hero. Avoid a static image feel while preserving the product.

## Negative Prompt
Write comma-separated negative keywords and phrases.

## Recommended Settings
- Duration:
- Aspect ratio:
- Motion:
- Style:

## Korean Notes
한국어로 짧게, 이 프롬프트의 의도와 Kling에서 주의할 점을 설명하세요.

## Regeneration Tip
If the first Kling take fails, write one short English instruction for the next generation attempt.
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
            {"role": "system", "content": "You create safe, faithful, practical ecommerce video-generation prompts. Never add text overlays to the video prompt. Preserve product identity and write practical prompts for Kling."},
            {"role": "user", "content": prompt_request},
        ],
        temperature=0.35,
    )
    return response.choices[0].message.content.strip()


def fallback_prompt() -> str:
    return """## Kling Main Prompt
Create a natural ecommerce lifestyle product video using the selected start and end images as reference. Keep the product design, color, silhouette, texture, material, pattern, and all details faithful to the reference images. If a model appears, keep the pose, outfit, body shape, and product placement realistic and natural. Use visible but gentle camera movement, slight parallax, and subtle lifestyle motion so the result does not feel like a static image. Keep the product as the hero and clearly visible. No text, no captions, no typography, no letters, no logos, no watermarks, no subtitles.

## Negative Prompt
wrong product shape, changed color, changed pattern, distorted product, deformed material, unnatural body pose, deformed hands, unrealistic clothing, extra objects, unrelated accessories, messy background, blurry details, excessive motion, static frame, no motion, text, letters, captions, typography, logo, watermark, subtitle

## Recommended Settings
- Duration: 5 seconds
- Aspect ratio: 1:1
- Motion: visible but gentle camera movement / slight parallax
- Style: clean ecommerce product video

## Korean Notes
OpenAI API Key가 입력되지 않아 기본 프롬프트를 표시했습니다. 상품 카테고리와 선택 이미지에 맞춰 세부 표현을 수동으로 조금 보정한 뒤 Kling에 입력해주세요.

## Regeneration Tip
Make the motion more subtle and keep the product perfectly faithful to the reference images.
"""


def build_regeneration_request(product: dict, scene_card: dict, issues: list[str], memo: str, generated_prompt: str) -> str:
    return f"""
You are helping improve a Kling AI product video generation prompt.
The previous generation had issues. Create a short English regeneration instruction to append to the next Kling prompt.
Do not rewrite the entire prompt. Focus only on correcting the issues.

Product: {product.get('title')}
Scene Card: {json.dumps(scene_card, ensure_ascii=False)}
Issues: {', '.join(issues)}
Operator memo: {memo}
Previous prompt:
{generated_prompt[:2500]}

Return this exact structure:
## Regeneration Instruction
One concise English paragraph.

## Korean Note
한국어로 왜 이렇게 수정해야 하는지 짧게 설명.
""".strip()


def generate_regeneration_instruction(request_text: str, model: str, api_key: str) -> str:
    if not api_key:
        return "## Regeneration Instruction\nMake the motion more subtle and keep the product perfectly faithful to the reference images. Do not change the product color, shape, pattern, strap, handle, material, or proportions. Avoid text, logos, extra objects, and dramatic transformations.\n\n## Korean Note\nAPI Key가 없어 기본 개선 프롬프트를 표시했습니다. 선택한 문제에 맞춰 수동으로 조금 보정해주세요."
    client = OpenAI(api_key=api_key.strip())
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You create concise corrective instructions for Kling AI product video regeneration."},
            {"role": "user", "content": request_text},
        ],
        temperature=0.25,
    )
    return response.choices[0].message.content.strip()


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


def create_zip(product_title: str, selected_images: list[dict], prompt_text: str, analysis: dict | None, selected_pair: dict | None, scene_card: dict | None) -> tuple[str, bytes]:
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
        if scene_card:
            zf.writestr("scene_card.json", json.dumps(scene_card, ensure_ascii=False, indent=2))
            zf.writestr("scene_card.md", scene_card_markdown(scene_card))
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
        "created_at", "product_url", "product_title", "category", "video_type", "video_strategy", "style", "ratio", "duration",
        "selected_image_ids", "selected_pair_score", "model", "prompt_hash", "video_created", "shopify_uploaded", "memo",
    ]
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fieldnames})


def append_take_log(row: dict) -> None:
    exists = os.path.exists(TAKE_LOG_FILE)
    fieldnames = [
        "created_at", "product_url", "product_title", "video_type", "take_no", "status",
        "issue_types", "final_rating", "kling_result_url", "shopify_uploaded", "memo",
    ]
    with open(TAKE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
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
    st.title("🧦 SocksLover Mini ShotFlow")
    st.caption("모델 착샷 우선 Pair 추천과 Kling 프롬프트를 관리하는 SocksLover용 Mini ShotFlow MVP v4.5")

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
        st.write("✅ Lifestyle First Pair 추천 자동화")
        st.write("✅ Scene Card 생성")
        st.write("✅ Product Lock / Model Lock 자동 삽입")
        st.write("✅ Kling 프롬프트 생성")
        st.write("✅ Take Log / 재생성 프롬프트")
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
                    for k in ["vision_analysis", "generated_prompt", "selected_pair", "selected_images", "prompt_meta", "scene_card", "regen_prompt"]:
                        st.session_state.pop(k, None)
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

    st.subheader("2. 영상 용도 / Product Lock")
    video_type = st.selectbox("영상 용도", list(VIDEO_TYPE_OPTIONS.keys()), index=0)
    video_strategy = st.selectbox("추천 전략", list(VIDEO_STRATEGY_OPTIONS.keys()), index=0, help="현재 상품 이미지 자산이 모델 착샷 중심이면 Lifestyle First를 추천합니다.")
    st.info(VIDEO_STRATEGY_OPTIONS.get(video_strategy, {}).get("summary", ""))
    vset = video_type_settings(video_type)
    vc1, vc2, vc3 = st.columns(3)
    vc1.metric("기본 비율", vset.get("ratio"))
    vc2.metric("기본 길이", vset.get("duration"))
    with vc3:
        st.write("**목표**")
        st.write(vset.get("goal"))
    with st.expander("Product Lock 자동 삽입 규칙 보기", expanded=False):
        for rule in product_lock_rules(category):
            st.write(f"- {rule}")

    st.subheader("3. 이미지 카드")
    st.caption("GPT Vision 분석 전에는 단순 이미지 목록만 표시됩니다. 분석 후에는 유형/적합도/추천 사유가 카드에 표시됩니다.")
    render_image_cards(product.get("images", []), st.session_state.get("vision_analysis"))

    st.subheader("4. GPT Vision 이미지 분석 & Pair 추천")
    st.caption("선택한 영상 용도 기준으로 Kling에서 시작/끝 프레임으로 쓰기 좋은 2장 조합을 추천합니다.")
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
                        analysis = analyze_images_and_pairs(product, images_for_analysis, category, video_type, video_strategy, model, api_key)
                        st.session_state["vision_analysis"] = analysis
                        st.success("분석 완료. 아래 추천 조합을 확인해주세요.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Vision 분석 실패: {e}")

    analysis = st.session_state.get("vision_analysis")
    selected_pair = None
    selected_images = []

    if analysis:
        st.subheader("5. 추천 Pair TOP 3")
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

    st.subheader("6. Scene Card / Kling 프롬프트 조건")
    c1, c2, c3 = st.columns(3)
    with c1:
        style = st.selectbox("영상 스타일", STYLE_OPTIONS, index=0)
    with c2:
        default_ratio = vset.get("ratio", "1:1")
        ratio = st.selectbox("추천 비율", RATIO_OPTIONS, index=RATIO_OPTIONS.index(default_ratio) if default_ratio in RATIO_OPTIONS else 0)
    with c3:
        default_duration = vset.get("duration", "5 seconds")
        duration = st.selectbox("영상 길이", DURATION_OPTIONS, index=DURATION_OPTIONS.index(default_duration) if default_duration in DURATION_OPTIONS else 0)

    st.caption("원칙: 영상 안에 텍스트/자막/로고를 넣지 않습니다. Kling에는 추천된 2장만 업로드하는 것을 권장합니다.")

    if selected_images:
        st.markdown("**현재 선택된 시작/끝 이미지**")
        s1, s2 = st.columns(2)
        with s1:
            st.image(selected_images[0]["url"], caption=f"Start: 사용 {selected_images[0].get('image_id')}", use_container_width=True)
        with s2:
            if len(selected_images) > 1:
                st.image(selected_images[1]["url"], caption=f"End: 사용 {selected_images[1].get('image_id')}", use_container_width=True)

    scene_card = None
    if selected_images and len(selected_images) >= 2:
        scene_card = build_scene_card(product, category, video_type, video_strategy, style, ratio, duration, selected_pair or st.session_state.get("selected_pair", {}), selected_images)
        st.markdown(scene_card_markdown(scene_card))
        st.session_state["scene_card"] = scene_card

    generate_clicked = st.button("Scene Card 기반 Kling용 프롬프트 생성", type="primary", use_container_width=True)

    if generate_clicked:
        if not selected_images or len(selected_images) < 2:
            st.error("먼저 추천 Pair를 선택해주세요. Kling 시작/끝 프레임에는 2장이 필요합니다.")
        else:
            with st.spinner("GPT가 Kling용 프롬프트를 생성하는 중입니다..."):
                prompt_request = build_prompt_request(product, selected_images, category, video_type, video_strategy, style, ratio, duration, selected_pair, analysis, scene_card)
                try:
                    result = generate_prompt_with_openai(prompt_request, model, api_key)
                except Exception as e:
                    result = f"프롬프트 생성 실패: {e}\n\n" + fallback_prompt()
                st.session_state["generated_prompt"] = result
                st.session_state["selected_images"] = selected_images
                st.session_state["prompt_meta"] = {"category": category, "video_type": video_type, "video_strategy": video_strategy, "style": style, "ratio": ratio, "duration": duration, "model": model}

    generated = st.session_state.get("generated_prompt")
    if generated:
        st.divider()
        st.subheader("7. 생성된 Kling 프롬프트")
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
                    "video_type": meta.get("video_type"),
                    "video_strategy": meta.get("video_strategy"),
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
                st.session_state.get("scene_card"),
            )
            st.download_button("선택 이미지 + 프롬프트 ZIP 다운로드", data=zip_bytes, file_name=zip_name, mime="application/zip", use_container_width=True)
        with d3:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "rb") as f:
                    st.download_button("CSV 로그 다운로드", data=f.read(), file_name="generation_log.csv", mime="text/csv", use_container_width=True)

        st.subheader("8. Kling Take Log / 재생성 프롬프트")
        with st.container(border=True):
            t1, t2, t3 = st.columns(3)
            with t1:
                take_no = st.number_input("Take 번호", min_value=1, value=1, step=1)
                take_status = st.selectbox("상태", TAKE_STATUS_OPTIONS, index=1)
            with t2:
                final_rating = st.slider("결과 평가", min_value=0, max_value=5, value=0, help="0=미평가, 5=채택 가능")
                shopify_uploaded = st.checkbox("Shopify 등록 완료")
            with t3:
                kling_result_url = st.text_input("Kling 결과 URL/파일명", placeholder="선택 입력")
            issue_types = st.multiselect("문제 유형", ISSUE_OPTIONS)
            take_memo = st.text_area("메모", placeholder="예: 스트랩이 하나 더 생김 / 제품은 안정적이나 배경이 복잡함")
            lc1, lc2 = st.columns(2)
            with lc1:
                if st.button("Take Log 저장", use_container_width=True):
                    meta = st.session_state.get("prompt_meta", {})
                    append_take_log({
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "product_url": product.get("url"),
                        "product_title": product.get("title"),
                        "video_type": meta.get("video_type"),
                        "take_no": take_no,
                        "status": take_status,
                        "issue_types": ", ".join(issue_types),
                        "final_rating": final_rating,
                        "kling_result_url": kling_result_url,
                        "shopify_uploaded": "yes" if shopify_uploaded else "",
                        "memo": take_memo,
                    })
                    st.success(f"Take Log 저장 완료: {TAKE_LOG_FILE}")
            with lc2:
                if os.path.exists(TAKE_LOG_FILE):
                    with open(TAKE_LOG_FILE, "rb") as f:
                        st.download_button("Take Log CSV 다운로드", data=f.read(), file_name="take_log.csv", mime="text/csv", use_container_width=True)

            if st.button("문제 기반 재생성 프롬프트 만들기", use_container_width=True):
                scene_card_for_regen = st.session_state.get("scene_card", {})
                req = build_regeneration_request(product, scene_card_for_regen, issue_types, take_memo, generated)
                try:
                    regen = generate_regeneration_instruction(req, model, api_key)
                except Exception as e:
                    regen = f"재생성 프롬프트 생성 실패: {e}"
                st.session_state["regen_prompt"] = regen
            if st.session_state.get("regen_prompt"):
                st.text_area("Kling 재생성용 추가 지시문", value=st.session_state.get("regen_prompt"), height=180)

        st.subheader("9. 수동 운영 체크리스트")
        st.markdown(
            """
- ZIP 안의 시작/끝 이미지 2장을 Kling에 업로드합니다.
- Scene Card를 보고 영상 목적과 Product Lock을 확인합니다.
- 생성된 Main Prompt / Negative Prompt를 Kling에 붙여넣습니다.
- 영상 안에 텍스트, 로고, 가짜 문자가 생기면 재생성합니다.
- 상품 디자인, 색상, 패턴, 스트랩, 소재감이 실제 상품과 다르면 사용하지 않습니다.
- 모델 착샷 기반 영상은 제품이 주인공으로 보이는지, 착용 맥락이 자연스러운지 확인합니다.
- Kling 결과를 Take Log에 기록하고, 채택 가능한 영상만 Shopify 상품 미디어에 수동 등록합니다.
            """.strip()
        )


if __name__ == "__main__":
    main()
