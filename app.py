import os
import re
import io
import csv
import json
import zipfile
import hashlib
import shutil
import subprocess
import tempfile
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


# =========================================================
# App configuration
# =========================================================

APP_TITLE = "SocksLover AI Content Engine"
APP_VERSION = "MVP v5.3 Scene Image Fallback"
DEFAULT_MODEL = "gpt-4.1-mini"

OUTPUT_DIR = "outputs"
ZIP_DIR = os.path.join(OUTPUT_DIR, "zips")
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "generation_log.csv")
TAKE_LOG_FILE = os.path.join(LOG_DIR, "take_log.csv")

os.makedirs(ZIP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

WORKFLOW_OPTIONS = [
    "Mini ShotFlow / 단일 5초 영상",
    "TikTok StoryFlow / 15초 영상",
]

CATEGORY_OPTIONS = ["Bag", "Socks", "Hat / Scarf", "Accessory", "Other"]

STYLE_OPTIONS = [
    "Clean ecommerce",
    "Cute lifestyle",
    "Premium minimal",
    "Daily outing",
]

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

TAKE_STATUS_OPTIONS = [
    "Prompt Ready",
    "Submitted to Kling",
    "Generated",
    "Accepted",
    "Rejected",
    "Uploaded to Shopify",
]

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


# =========================================================
# General utilities
# =========================================================

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_filename(text: str, max_len: int = 90) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣ぁ-んァ-ヶ一-龥._-]+", "-", text or "")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned[:max_len] or "product"


def json_from_text(text: str) -> dict:
    text = (text or "").strip()
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


def openai_client(api_key: str) -> OpenAI:
    if not api_key:
        raise ValueError("OpenAI API Key를 입력해주세요.")
    if OpenAI is None:
        raise RuntimeError("OpenAI 패키지를 불러오지 못했습니다. requirements.txt를 확인해주세요.")
    return OpenAI(api_key=api_key.strip())


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# =========================================================
# Shopify product extraction
# =========================================================

def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


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
        if any(pattern in lowered for pattern in blocked_patterns):
            continue

        if not any(
            ext in lowered
            for ext in [
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                "cdn.shopify.com",
                "alicdn",
                "ae01",
            ]
        ):
            continue

        dedupe_key = re.sub(
            r"_(\d+x\d+|\d+x|x\d+)\.(jpg|jpeg|png|webp)",
            r".\2",
            raw_url,
            flags=re.I,
        )
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
            if not isinstance(item, dict):
                continue

            item_type = item.get("@type")
            if item_type == "Product" or (
                isinstance(item_type, list) and "Product" in item_type
            ):
                product_json = item
                break

        if product_json:
            break

    if product_json:
        title = title or normalize_space(product_json.get("name", ""))
        description = description or normalize_space(
            product_json.get("description", "")
        )

        offers = product_json.get("offers")
        if not price and isinstance(offers, dict):
            price = str(offers.get("price", ""))
        elif not price and isinstance(offers, list) and offers:
            price = str(offers[0].get("price", ""))

    images = []

    json_images = product_json.get("image", []) if product_json else []
    if isinstance(json_images, str):
        json_images = [json_images]

    for img_url in json_images:
        if isinstance(img_url, str):
            images.append(
                {
                    "url": urljoin(url, img_url),
                    "alt": title,
                    "source": "json-ld",
                }
            )

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        images.append(
            {
                "url": urljoin(url, og_image.get("content")),
                "alt": title,
                "source": "og:image",
            }
        )

    for img in soup.find_all("img"):
        candidates = [
            img.get("src"),
            img.get("data-src"),
            img.get("data-original"),
            img.get("data-zoom"),
        ]

        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            srcset_urls = [
                part.strip().split(" ")[0]
                for part in srcset.split(",")
                if part.strip()
            ]
            candidates.extend(srcset_urls[-2:])

        for candidate in candidates:
            if not candidate:
                continue

            full_url = urljoin(url, candidate)
            alt = normalize_space(img.get("alt", ""))

            images.append(
                {
                    "url": full_url,
                    "alt": alt,
                    "source": "img-tag",
                }
            )

    images = dedupe_and_filter_images(images)

    for index, image in enumerate(images, start=1):
        image["image_id"] = str(index)
        image["label"] = f"사용 {index}"

    return {
        "url": url,
        "title": title,
        "description": description,
        "price": price,
        "images": images,
    }


def infer_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()

    if any(
        keyword in text
        for keyword in [
            "bag",
            "バッグ",
            "鞄",
            "かばん",
            "ショルダー",
            "トート",
            "ポーチ",
        ]
    ):
        return "Bag"

    if any(
        keyword in text
        for keyword in [
            "sock",
            "socks",
            "靴下",
            "ソックス",
            "くつ下",
        ]
    ):
        return "Socks"

    if any(
        keyword in text
        for keyword in [
            "hat",
            "cap",
            "scarf",
            "帽子",
            "ハット",
            "キャップ",
            "スカーフ",
        ]
    ):
        return "Hat / Scarf"

    return "Other"


# =========================================================
# Image analysis and pair recommendation
# =========================================================

def video_type_settings(video_type: str) -> dict:
    return VIDEO_TYPE_OPTIONS.get(
        video_type,
        VIDEO_TYPE_OPTIONS["Shopify Product Video"],
    )


def product_lock_rules(category: str) -> list[str]:
    rules = [
        "Do not add any text, captions, typography, letters, logos, watermarks, or subtitles.",
        "Keep the original product design, color, silhouette, texture, material, and proportions faithful to the reference images.",
        "Use subtle, realistic motion only; avoid dramatic transformation or fantasy effects.",
        "Keep the background clean and avoid unrelated accessories or objects.",
        "If a model appears, keep the body, pose, outfit, and product placement natural and realistic.",
        "The model is only a context; the product must remain the hero and clearly visible.",
    ]

    if category == "Bag":
        rules += [
            "Do not change the bag strap, handle, buckle, zipper, stitching, pocket position, or overall structure.",
            "Do not add extra straps, extra handles, charms, logos, or hardware that are not visible in the reference images.",
        ]

    elif category == "Socks":
        rules += [
            "Do not change the sock pattern, color, length, fabric texture, ribbing, transparency, or pair structure.",
            "Avoid unrealistic feet, distorted legs, or fabric deformation.",
        ]

    elif category == "Hat / Scarf":
        rules += [
            "Do not change the shape, brim, weave, fabric texture, pattern, or drape of the item.",
        ]

    return rules


def audio_lock_rules() -> list[str]:
    return [
        "Generate silent video only.",
        "No music.",
        "No background music.",
        "No dialogue.",
        "No voice-over.",
        "No sound effects.",
        "Do not generate ambient sound or environmental audio.",
    ]


def build_vision_messages(
    product: dict,
    images_for_analysis: list[dict],
    category: str,
    video_type: str,
    video_strategy: str,
) -> list[dict]:
    settings = video_type_settings(video_type)

    instructions = f"""
You are an ecommerce creative director and image selector for Kling AI image-to-video generation.
The store is SocksLover, a Japanese Shopify store.
Kling will use exactly 2 images: one start frame and one end frame.

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
- Analyze whether each image is relevant to the current product.
- Mark default/common/recommended-product images with low relevance and is_global_default=true.
- For bags, socks-only images are unrelated.
- For socks, bag-only images are unrelated.
- NEVER recommend the same image as both start and end.
- Lifestyle First: prioritize model_shot + model_shot when the same product/color is visible.
- Balanced: prioritize model_shot + single_product or single_product + model_shot.
- Product Focus: prioritize single_product + single_product.
- Prefer model/lifestyle shots where the product is clearly visible.
- Avoid face/body-dominant shots where the product is too small.
- Avoid collage or multi-product images unless no alternative exists.
- For bags, good pairs include wearing shot -> similar wearing shot, or product shot -> wearing shot.
- For socks, prefer wearing/fit shots with similar pose and background.
- Return practical recommendations even if quality is imperfect.

Return JSON only:
{{
  "images": [
    {{
      "image_id": "1",
      "image_type": "single_product | model_shot | multi_product | collage | detail_shot | lifestyle | unclear",
      "suitability_score": 0,
      "relevance_score": 0,
      "is_global_default": false,
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
  "scene_image_recommendations": [
    {{
      "scene_number": 1,
      "image_id": "1",
      "image_score": 0,
      "reason_ko": "Why this single image is suitable for this scene",
      "cautions_ko": "Korean caution or empty string",
      "suggested_motion": "subtle natural motion suitable for a single-image Kling generation"
    }}
  ],
  "overall_notes_ko": "Short Korean advice for the operator"
}}

For TikTok StoryFlow, recommend exactly one reference image for each of three scenes.
Return three scene_image_recommendations with scene_number 1, 2, and 3.
Prefer three different relevant images when possible, but reuse one only when the image inventory is limited.
The recommended image must clearly show the current product and be suitable as a single Kling reference image.
Also return up to 5 pair recommendations for the legacy Mini ShotFlow mode.
Do not return any pair where start_image_id and end_image_id are the same.
""".strip()

    content = [{"type": "text", "text": instructions}]

    for image in images_for_analysis:
        content.append(
            {
                "type": "text",
                "text": (
                    f"Image ID: {image['image_id']} / "
                    f"label: {image.get('label', '')} / "
                    f"source: {image.get('source', '')} / "
                    f"alt: {image.get('alt', '')}"
                ),
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image["url"],
                    "detail": "low",
                },
            }
        )

    return [{"role": "user", "content": content}]


def analyze_images_and_pairs(
    product: dict,
    images_for_analysis: list[dict],
    category: str,
    video_type: str,
    video_strategy: str,
    model: str,
    api_key: str,
) -> dict:
    client = openai_client(api_key)

    response = client.chat.completions.create(
        model=model,
        messages=build_vision_messages(
            product,
            images_for_analysis,
            category,
            video_type,
            video_strategy,
        ),
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    return json_from_text(response.choices[0].message.content)


def get_analysis_for_image(analysis: dict, image_id: str) -> dict:
    for item in analysis.get("images", []):
        if str(item.get("image_id")) == str(image_id):
            return item
    return {}


def image_type_weight(image_type: str, video_strategy: str) -> int:
    image_type = (image_type or "unclear").strip()

    if video_strategy.startswith("Lifestyle First"):
        weights = {
            "model_shot": 45,
            "lifestyle": 42,
            "single_product": 24,
            "detail_shot": 8,
            "multi_product": -25,
            "collage": -35,
            "unclear": -5,
        }

    elif video_strategy.startswith("Balanced"):
        weights = {
            "model_shot": 36,
            "lifestyle": 34,
            "single_product": 32,
            "detail_shot": 10,
            "multi_product": -22,
            "collage": -35,
            "unclear": -5,
        }

    else:
        weights = {
            "single_product": 42,
            "model_shot": 26,
            "lifestyle": 24,
            "detail_shot": 12,
            "multi_product": -25,
            "collage": -35,
            "unclear": -5,
        }

    return weights.get(image_type, -5)


def combo_bonus(
    start_type: str,
    end_type: str,
    video_strategy: str,
) -> int:
    types = {start_type, end_type}

    if video_strategy.startswith("Lifestyle First"):
        if (
            start_type in ["model_shot", "lifestyle"]
            and end_type in ["model_shot", "lifestyle"]
        ):
            return 34

        if (
            start_type in ["model_shot", "lifestyle"]
            and end_type == "single_product"
        ) or (
            end_type in ["model_shot", "lifestyle"]
            and start_type == "single_product"
        ):
            return 24

        if types == {"single_product"}:
            return 6

    elif video_strategy.startswith("Balanced"):
        if (
            start_type in ["model_shot", "lifestyle"]
            and end_type == "single_product"
        ) or (
            end_type in ["model_shot", "lifestyle"]
            and start_type == "single_product"
        ):
            return 30

        if (
            start_type in ["model_shot", "lifestyle"]
            and end_type in ["model_shot", "lifestyle"]
        ):
            return 24

        if types == {"single_product"}:
            return 16

    else:
        if types == {"single_product"}:
            return 30

        if (
            start_type in ["model_shot", "lifestyle"]
            and end_type == "single_product"
        ) or (
            end_type in ["model_shot", "lifestyle"]
            and start_type == "single_product"
        ):
            return 16

    return 0


def build_heuristic_pairs(
    images: list[dict],
    analysis: dict,
    video_strategy: str,
    limit: int = 6,
) -> list[dict]:
    image_analysis = {
        str(item.get("image_id")): item
        for item in analysis.get("images", [])
    }

    candidates = []
    valid_ids = [str(image.get("image_id")) for image in images]

    for start_id in valid_ids:
        for end_id in valid_ids:
            if start_id == end_id:
                continue

            start_analysis = image_analysis.get(start_id, {})
            end_analysis = image_analysis.get(end_id, {})

            start_type = start_analysis.get("image_type", "unclear")
            end_type = end_analysis.get("image_type", "unclear")

            if (
                start_analysis.get("is_global_default")
                or end_analysis.get("is_global_default")
            ):
                continue

            if int(start_analysis.get("relevance_score") or 100) < 30:
                continue

            if int(end_analysis.get("relevance_score") or 100) < 30:
                continue

            base_score = (
                int(start_analysis.get("suitability_score") or 0)
                + int(end_analysis.get("suitability_score") or 0)
            ) / 2

            score = (
                base_score
                + image_type_weight(start_type, video_strategy)
                + image_type_weight(end_type, video_strategy)
                + combo_bonus(start_type, end_type, video_strategy)
            )

            if (
                start_type in ["multi_product", "collage"]
                and end_type in ["multi_product", "collage"]
            ):
                score -= 45

            if (
                start_type in ["multi_product", "collage"]
                or end_type in ["multi_product", "collage"]
            ):
                score -= 20

            candidates.append(
                {
                    "rank": 0,
                    "start_image_id": start_id,
                    "end_image_id": end_id,
                    "pair_score": max(
                        0,
                        min(100, int(round(score / 2))),
                    ),
                    "reason_ko": (
                        "자동 보정 추천: 분석 결과를 기준으로 동일 이미지 조합을 제외하고, "
                        "현재 전략에 맞는 이미지 유형 조합을 우선했습니다."
                    ),
                    "cautions_ko": (
                        "GPT 추천 보정 결과입니다. 실제 상품과 색상이 동일한지 확인해주세요."
                    ),
                    "suggested_motion": (
                        "visible but natural camera movement / "
                        "gentle lifestyle motion / slight parallax"
                    ),
                    "source": "heuristic_guard",
                }
            )

    candidates.sort(
        key=lambda item: item.get("pair_score", 0),
        reverse=True,
    )

    return candidates[:limit]


def normalize_pair_recommendations(
    product_images: list[dict],
    analysis: dict,
    video_strategy: str,
) -> dict:
    if not analysis:
        return analysis

    valid_ids = {
        str(image.get("image_id"))
        for image in product_images
    }

    cleaned = []
    seen = set()

    for pair in analysis.get("pair_recommendations", []) or []:
        start_id = str(pair.get("start_image_id", "")).strip()
        end_id = str(pair.get("end_image_id", "")).strip()

        if (
            not start_id
            or not end_id
            or start_id not in valid_ids
            or end_id not in valid_ids
        ):
            continue

        if start_id == end_id:
            continue

        key = (start_id, end_id)
        if key in seen:
            continue

        seen.add(key)
        pair["start_image_id"] = start_id
        pair["end_image_id"] = end_id
        pair["source"] = pair.get("source", "gpt_vision")
        cleaned.append(pair)

    heuristic_pairs = build_heuristic_pairs(
        product_images,
        analysis,
        video_strategy,
        limit=8,
    )

    for pair in heuristic_pairs:
        key = (
            str(pair.get("start_image_id")),
            str(pair.get("end_image_id")),
        )

        if key not in seen:
            seen.add(key)
            cleaned.append(pair)

    cleaned.sort(
        key=lambda item: int(item.get("pair_score") or 0),
        reverse=True,
    )

    cleaned = cleaned[:3]

    for index, pair in enumerate(cleaned, start=1):
        pair["rank"] = index

    analysis["pair_recommendations"] = cleaned

    if not cleaned:
        analysis["overall_notes_ko"] = (
            analysis.get("overall_notes_ko", "")
            + " 추천 가능한 서로 다른 이미지 Pair가 없습니다."
        ).strip()
    else:
        analysis["overall_notes_ko"] = (
            analysis.get("overall_notes_ko", "")
            + " 동일 이미지 Start/End 조합은 자동 제외했습니다."
        ).strip()

    return analysis



def normalize_scene_image_recommendations(
    product_images: list[dict],
    analysis: dict,
    scene_count: int = 3,
) -> dict:
    """Always provide one valid single reference image for every StoryFlow scene.

    GPT may omit scene_image_recommendations or optional scores. In that case,
    this function falls back to the Vision image list and finally to the first
    valid Shopify product images, so the Storyboard Editor never stays empty.
    """
    analysis = analysis or {}

    valid_images = [
        image for image in product_images
        if image.get("url") and image.get("image_id") is not None
    ]
    valid_ids = {str(image.get("image_id")) for image in valid_images}
    image_analysis = {
        str(item.get("image_id")): item
        for item in analysis.get("images", [])
        if item.get("image_id") is not None
    }

    by_scene: dict[int, dict] = {}
    used_ids: set[str] = set()

    # 1. Preserve valid GPT scene-level recommendations.
    for item in analysis.get("scene_image_recommendations", []) or []:
        try:
            scene_number = int(item.get("scene_number") or 0)
        except (TypeError, ValueError):
            continue

        image_id = str(item.get("image_id", "")).strip()
        if not (1 <= scene_number <= scene_count):
            continue
        if image_id not in valid_ids:
            continue

        judged = image_analysis.get(image_id, {})
        if judged.get("is_global_default") is True:
            continue

        relevance_raw = judged.get("relevance_score")
        relevance = 70 if relevance_raw in (None, "") else int(relevance_raw or 0)
        if relevance < 30:
            continue

        candidate = dict(item)
        candidate["image_id"] = image_id
        candidate.setdefault("image_score", judged.get("suitability_score", 70) or 70)
        candidate.setdefault("reason_ko", "GPT Vision이 해당 Scene에 적합한 단일 기준 이미지로 추천했습니다.")
        candidate.setdefault("cautions_ko", "실제 상품 색상과 패턴을 최종 확인해주세요.")
        candidate.setdefault("suggested_motion", "subtle natural motion, gentle camera push-in, slight parallax")

        current = by_scene.get(scene_number)
        if current is None or int(candidate.get("image_score") or 0) > int(current.get("image_score") or 0):
            by_scene[scene_number] = candidate
            used_ids.add(image_id)

    # 2. Rank Vision-analyzed images. Missing optional scores receive safe defaults.
    ranked_images: list[tuple[int, str, str]] = []
    for image in valid_images:
        image_id = str(image.get("image_id"))
        judged = image_analysis.get(image_id, {})

        if judged.get("is_global_default") is True:
            continue

        relevance_raw = judged.get("relevance_score")
        suitability_raw = judged.get("suitability_score")
        relevance = 70 if relevance_raw in (None, "") else int(relevance_raw or 0)
        suitability = 60 if suitability_raw in (None, "") else int(suitability_raw or 0)
        image_type = judged.get("image_type", "unclear")

        if relevance < 30:
            continue

        type_bonus = {
            "model_shot": 24,
            "lifestyle": 22,
            "single_product": 18,
            "detail_shot": 10,
            "multi_product": -15,
            "collage": -25,
            "unclear": 0,
        }.get(image_type, 0)

        ranked_images.append(
            (suitability + relevance + type_bonus, image_id, image_type)
        )

    ranked_images.sort(key=lambda item: item[0], reverse=True)

    # 3. Absolute fallback: use the first Shopify product images even when
    # Vision omitted its image array or all optional scores.
    if not ranked_images:
        ranked_images = [
            (70 - index, str(image.get("image_id")), "unclassified")
            for index, image in enumerate(valid_images)
        ]

    for scene_number in range(1, scene_count + 1):
        if scene_number in by_scene:
            continue

        selected = next(
            (item for item in ranked_images if item[1] not in used_ids),
            None,
        )
        if selected is None and ranked_images:
            selected = ranked_images[(scene_number - 1) % len(ranked_images)]

        if selected is None:
            continue

        score, image_id, image_type = selected
        used_ids.add(image_id)
        by_scene[scene_number] = {
            "scene_number": scene_number,
            "image_id": image_id,
            "image_score": max(0, min(100, int(score / 2))),
            "reason_ko": (
                "자동 보정 추천: 상품과 관련된 이미지 중 단일 이미지 기반 "
                f"Kling 생성에 적합한 {image_type} 이미지를 배정했습니다."
            ),
            "cautions_ko": "실제 상품 색상과 패턴이 정확한지 최종 확인해주세요.",
            "suggested_motion": (
                "subtle natural movement, gentle camera push-in, "
                "slight parallax, keep the product perfectly faithful"
            ),
            "source": "single_image_fallback_guard",
        }

    analysis["scene_image_recommendations"] = [
        by_scene[scene_number]
        for scene_number in range(1, scene_count + 1)
        if scene_number in by_scene
    ]
    return analysis


def scene_image_label(item: dict) -> str:
    return (
        f"Scene {item.get('scene_number')} 추천 | "
        f"이미지 {item.get('image_id')} | "
        f"{item.get('image_score')}점"
    )


def selected_image_by_id(images: list[dict], image_id: str) -> dict | None:
    for image in images:
        if str(image.get("image_id")) == str(image_id):
            return image
    return None

def pair_label(pair: dict) -> str:
    return (
        f"추천 {pair.get('rank')} | "
        f"시작 {pair.get('start_image_id')} → "
        f"끝 {pair.get('end_image_id')} | "
        f"{pair.get('pair_score')}점"
    )


def selected_images_from_pair(
    images: list[dict],
    pair: dict | None,
) -> list[dict]:
    if not pair:
        return []

    ids = [
        str(pair.get("start_image_id")),
        str(pair.get("end_image_id")),
    ]

    selected = []

    for image_id in ids:
        for image in images:
            if str(image.get("image_id")) == image_id:
                selected.append(image)
                break

    return selected


# =========================================================
# Content strategy and storyboard
# =========================================================

def build_content_strategy_request(
    product: dict,
    category: str,
    analysis: dict | None,
) -> str:
    image_summary = []

    for item in (analysis or {}).get("images", []):
        image_summary.append(
            {
                "image_id": item.get("image_id"),
                "image_type": item.get("image_type"),
                "suitability_score": item.get("suitability_score"),
                "relevance_score": item.get("relevance_score"),
                "recommended_role": item.get("recommended_role"),
            }
        )

    return f"""
You are a TikTok Shop Japan short-form commerce strategist.

Analyze the Shopify product and its available image assets.
Choose the most suitable strategy for one 15-second vertical commerce video.

Product:
- Title: {product.get('title')}
- Description: {product.get('description')}
- Price: {product.get('price')}
- Category: {category}

Image analysis:
{json.dumps(image_summary, ensure_ascii=False, indent=2)}

Available content themes:
1. Shoes × Socks Styling
2. Before / After
3. One Pair, Three Outfits
4. Color Comparison
5. Unboxing / ASMR
6. Product Detail

Decide:
- content theme
- target audience
- recommended shoes
- visual style
- primary selling point
- subtitle tone
- CTA
- recommendation reason

Rules:
- Platform is TikTok Shop Japan.
- Aspect ratio is always 9:16.
- Total duration is always 15 seconds.
- Use exactly three scenes of five seconds each.
- Kling videos must be completely silent.
- Do not invent product properties or benefits.
- The selected strategy should help viewers imagine wearing or using the product.
- For fashion socks, prefer styling, outfit transformation, or wearing ideas when suitable images exist.
- Write customer-facing captions and CTA in natural Japanese.

Return JSON only:
{{
  "platform": "TikTok Shop",
  "content_theme": "",
  "target_audience": "",
  "recommended_shoes": "",
  "visual_style": "",
  "primary_selling_point": "",
  "subtitle_tone": "",
  "cta_ja": "",
  "aspect_ratio": "9:16",
  "total_duration": 15,
  "scene_count": 3,
  "audio_policy": "silent",
  "recommendation_reason_ko": ""
}}
""".strip()


def generate_content_strategy(
    product: dict,
    category: str,
    analysis: dict | None,
    model: str,
    api_key: str,
) -> dict:
    client = openai_client(api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create evidence-based TikTok Shop Japan "
                    "content strategies for ecommerce products."
                ),
            },
            {
                "role": "user",
                "content": build_content_strategy_request(
                    product,
                    category,
                    analysis,
                ),
            },
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    strategy = json_from_text(
        response.choices[0].message.content
    )

    strategy["platform"] = "TikTok Shop"
    strategy["aspect_ratio"] = "9:16"
    strategy["total_duration"] = 15
    strategy["scene_count"] = 3
    strategy["audio_policy"] = "silent"

    return strategy


def build_storyboard_request(
    product: dict,
    category: str,
    strategy: dict,
    image_analysis: dict | None,
) -> str:
    image_summary = []

    for item in (image_analysis or {}).get("images", []):
        image_summary.append(
            {
                "image_id": item.get("image_id"),
                "image_type": item.get("image_type"),
                "suitability_score": item.get("suitability_score"),
                "relevance_score": item.get("relevance_score"),
            }
        )

    locks = product_lock_rules(category) + audio_lock_rules()

    return f"""
You are a TikTok Shop Japan fashion video director.

Create one 15-second storyboard divided into exactly three scenes of five seconds each.

Product:
- Title: {product.get('title')}
- Description: {product.get('description')}
- Price: {product.get('price')}
- Category: {category}

Content strategy:
{json.dumps(strategy, ensure_ascii=False, indent=2)}

Image asset summary:
{json.dumps(image_summary, ensure_ascii=False, indent=2)}

Mandatory structure:
- Scene 1: Hook and viewer problem or styling curiosity
- Scene 2: Styling transformation or product value
- Scene 3: Product detail, finished look, and CTA

Continuity rules:
- Keep the exact same product.
- Keep the same product color and pattern.
- Keep the same model, shoes, outfit, lighting, and background whenever model shots are used.
- Each scene must have a clear starting state and ending state.
- Each next scene should naturally follow the previous scene.
- Use simple motion that Kling can generate reliably.
- Do not use running, jumping, spinning, or complex hand movements.
- Do not invent product features.
- Captions must be short natural Japanese.
- Kling prompts must be written in English.
- Every Kling prompt must explicitly request a silent video.
- Do not put text or subtitles inside the Kling-generated video.

Product and audio locks:
{json.dumps(locks, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "content_title_ja": "",
  "content_summary_ko": "",
  "scenes": [
    {{
      "scene_number": 1,
      "role": "hook_before",
      "duration": 5,
      "caption_ja": "",
      "visual_description": "",
      "action": "",
      "camera": "",
      "starting_state": "",
      "ending_state": "",
      "continuity_instruction": "",
      "recommended_start_image_type": "",
      "recommended_end_image_type": "",
      "kling_prompt": "",
      "negative_prompt": ""
    }}
  ]
}}
""".strip()


def generate_storyboard(
    product: dict,
    category: str,
    strategy: dict,
    analysis: dict | None,
    model: str,
    api_key: str,
) -> dict:
    client = openai_client(api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create practical product-faithful "
                    "TikTok Shop Japan storyboards."
                ),
            },
            {
                "role": "user",
                "content": build_storyboard_request(
                    product,
                    category,
                    strategy,
                    analysis,
                ),
            },
        ],
        temperature=0.25,
        response_format={"type": "json_object"},
    )

    result = json_from_text(
        response.choices[0].message.content
    )

    scenes = result.get("scenes", [])

    if len(scenes) != 3:
        raise ValueError(
            f"Storyboard에는 정확히 3개 Scene이 필요합니다. 현재 {len(scenes)}개입니다."
        )

    for expected_number, scene in enumerate(
        scenes,
        start=1,
    ):
        scene["scene_number"] = expected_number
        scene["duration"] = 5
        scene["audio_policy"] = "silent"

        prompt = scene.get("kling_prompt", "").strip()
        if "silent" not in prompt.lower():
            prompt += (
                "\n\nSilent video only. "
                "No music, no background music, no dialogue, "
                "no voice-over, no sound effects, and no ambient audio."
            )
            scene["kling_prompt"] = prompt.strip()

    return result


def assign_default_images_to_storyboard(
    storyboard: dict,
    scene_image_recommendations: list[dict],
) -> dict:
    """Assign one reference image to each StoryFlow scene."""
    scenes = storyboard.get("scenes", [])
    recommendations = {
        int(item.get("scene_number")): item
        for item in scene_image_recommendations
        if item.get("scene_number")
    }

    for scene in scenes:
        scene_number = int(scene.get("scene_number") or 0)
        recommendation = recommendations.get(scene_number)
        if not recommendation:
            continue

        scene["reference_image_id"] = recommendation.get("image_id")
        scene["image_score"] = recommendation.get("image_score")
        scene["image_reason_ko"] = recommendation.get("reason_ko")
        scene["image_cautions_ko"] = recommendation.get("cautions_ko")
        scene["suggested_motion"] = recommendation.get("suggested_motion")

        prompt = scene.get("kling_prompt", "").strip()
        single_image_instruction = (
            " Use the selected single reference image as the only visual source. "
            "Do not create a start-to-end morph or transition between two images."
        )
        if "single reference image" not in prompt.lower():
            scene["kling_prompt"] = (prompt + single_image_instruction).strip()

    return storyboard


# =========================================================
# Mini ShotFlow prompt generation
# =========================================================

def build_scene_card(
    product: dict,
    category: str,
    video_type: str,
    video_strategy: str,
    style: str,
    ratio: str,
    duration: str,
    selected_pair: dict | None,
    selected_images: list[dict],
) -> dict:
    settings = video_type_settings(video_type)

    start_id = (
        selected_pair.get("start_image_id")
        if selected_pair
        else (
            selected_images[0].get("image_id")
            if selected_images
            else ""
        )
    )

    end_id = (
        selected_pair.get("end_image_id")
        if selected_pair
        else (
            selected_images[1].get("image_id")
            if len(selected_images) > 1
            else ""
        )
    )

    return {
        "product_title": product.get("title", ""),
        "product_url": product.get("url", ""),
        "category": category,
        "video_type": video_type,
        "video_strategy": video_strategy,
        "strategy_summary": VIDEO_STRATEGY_OPTIONS.get(
            video_strategy,
            {},
        ).get("summary", ""),
        "goal": settings.get("goal", ""),
        "style": style,
        "ratio": ratio,
        "duration": duration,
        "start_image_id": start_id,
        "end_image_id": end_id,
        "camera_motion": (
            selected_pair.get(
                "suggested_motion",
                "subtle camera movement",
            )
            if selected_pair
            else "subtle camera movement"
        ),
        "lighting": "soft natural light",
        "background": "clean minimal ecommerce background",
        "product_lock": product_lock_rules(category),
        "audio_lock": audio_lock_rules(),
        "cautions": (
            selected_pair.get("cautions_ko", "")
            if selected_pair
            else ""
        ),
    }


def scene_card_markdown(scene_card: dict) -> str:
    product_locks = "\n".join(
        f"- {rule}"
        for rule in scene_card.get("product_lock", [])
    )

    audio_locks = "\n".join(
        f"- {rule}"
        for rule in scene_card.get("audio_lock", [])
    )

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
{product_locks}

**Audio Lock**
{audio_locks}

**Cautions:** {scene_card.get('cautions') or '-'}
""".strip()


def build_prompt_request(
    product: dict,
    selected_images: list[dict],
    category: str,
    video_type: str,
    video_strategy: str,
    style: str,
    ratio: str,
    duration: str,
    selected_pair: dict | None,
    image_analysis: dict | None,
    scene_card: dict | None,
) -> str:
    image_notes = []

    for image in selected_images:
        analysis_item = get_analysis_for_image(
            image_analysis or {},
            image.get("image_id"),
        )

        image_notes.append(
            (
                f"- Image {image.get('image_id')}: "
                f"source={image.get('source', '')}, "
                f"alt={image.get('alt', '')}, "
                f"type={analysis_item.get('image_type', '')}, "
                f"score={analysis_item.get('suitability_score', '')}, "
                f"url={image.get('url', '')}"
            )
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

Create a practical Kling prompt using exactly the selected start and end reference images.

Important:
- The video must NOT include text, captions, typography, letters, logos, watermarks, or subtitles.
- The product must remain faithful to the reference images.
- The video must be completely silent.

Product:
- Product URL: {product.get('url')}
- Product title: {product.get('title')}
- Product description: {product.get('description')}
- Category: {category}
- Video type: {video_type}
- Video strategy: {video_strategy}
- Desired style: {style}
- Desired aspect ratio: {ratio}
- Desired duration: {duration}

Selected reference images:
{chr(10).join(image_notes)}

Product Lock:
{chr(10).join('- ' + rule for rule in product_lock_rules(category))}

Audio Lock:
{chr(10).join('- ' + rule for rule in audio_lock_rules())}

Scene Card:
{json.dumps(scene_card or {}, ensure_ascii=False, indent=2)}

{pair_notes}

Return this exact structure:

## Kling Main Prompt
Write one polished English prompt that can be copied directly into Kling.

## Negative Prompt
Write comma-separated negative keywords and phrases.
Include music, dialogue, voice-over, sound effects, and ambient audio.

## Recommended Settings
- Duration:
- Aspect ratio:
- Motion:
- Style:

## Korean Notes
한국어로 짧게 의도와 주의점을 설명하세요.

## Regeneration Tip
Write one short English instruction for the next generation attempt.
""".strip()


def generate_prompt_with_openai(
    prompt_request: str,
    model: str,
    api_key: str,
) -> str:
    if not api_key:
        return fallback_prompt()

    client = openai_client(api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create safe, faithful, practical ecommerce "
                    "video-generation prompts. Preserve product identity. "
                    "Always request a completely silent video."
                ),
            },
            {
                "role": "user",
                "content": prompt_request,
            },
        ],
        temperature=0.35,
    )

    return response.choices[0].message.content.strip()


def fallback_prompt() -> str:
    return """## Kling Main Prompt
Create a natural ecommerce lifestyle product video using the selected start and end images as references. Keep the product design, color, silhouette, texture, material, pattern, length, and all visible details faithful to the reference images. Use visible but gentle camera movement, subtle parallax, and realistic natural motion. Keep the product as the hero and clearly visible. No text, captions, typography, letters, logos, watermarks, or subtitles.

Generate silent video only. No music, no background music, no dialogue, no voice-over, no sound effects, and no ambient audio.

## Negative Prompt
wrong product shape, changed color, changed pattern, distorted product, deformed material, unnatural body pose, deformed hands, distorted feet, extra objects, unrelated accessories, messy background, blurry details, excessive motion, text, letters, captions, logo, watermark, subtitle, music, dialogue, voice-over, sound effects, ambient audio

## Recommended Settings
- Duration: 5 seconds
- Aspect ratio: 9:16
- Motion: visible but gentle natural motion
- Style: TikTok fashion ecommerce video

## Korean Notes
기본 무음 Kling 프롬프트입니다. 최종 음악과 효과음은 CapCut에서 추가합니다.

## Regeneration Tip
Keep the product perfectly faithful and generate a completely silent video.
"""


# =========================================================
# Regeneration and logging
# =========================================================

def build_regeneration_request(
    product: dict,
    scene_card: dict,
    issues: list[str],
    memo: str,
    generated_prompt: str,
) -> str:
    return f"""
You are helping improve a Kling AI product video generation prompt.
The previous generation had issues.

Create a short English regeneration instruction to append to the next Kling prompt.
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


def generate_regeneration_instruction(
    request_text: str,
    model: str,
    api_key: str,
) -> str:
    if not api_key:
        return (
            "## Regeneration Instruction\n"
            "Make the motion more subtle and keep the product perfectly faithful "
            "to the reference images. Do not change the product color, shape, "
            "pattern, material, or proportions. Keep the video completely silent.\n\n"
            "## Korean Note\n"
            "API Key가 없어 기본 개선 프롬프트를 표시했습니다."
        )

    client = openai_client(api_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create concise corrective instructions "
                    "for Kling AI product video regeneration."
                ),
            },
            {
                "role": "user",
                "content": request_text,
            },
        ],
        temperature=0.25,
    )

    return response.choices[0].message.content.strip()


def append_log(row: dict) -> None:
    exists = os.path.exists(LOG_FILE)

    fieldnames = [
        "created_at",
        "product_url",
        "product_title",
        "category",
        "video_type",
        "video_strategy",
        "style",
        "ratio",
        "duration",
        "selected_image_ids",
        "selected_pair_score",
        "model",
        "prompt_hash",
        "video_created",
        "shopify_uploaded",
        "memo",
    ]

    with open(
        LOG_FILE,
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        if not exists:
            writer.writeheader()

        writer.writerow(
            {
                key: row.get(key, "")
                for key in fieldnames
            }
        )


def append_take_log(row: dict) -> None:
    exists = os.path.exists(TAKE_LOG_FILE)

    fieldnames = [
        "created_at",
        "product_url",
        "product_title",
        "video_type",
        "take_no",
        "status",
        "issue_types",
        "final_rating",
        "kling_result_url",
        "shopify_uploaded",
        "memo",
    ]

    with open(
        TAKE_LOG_FILE,
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        if not exists:
            writer.writeheader()

        writer.writerow(
            {
                key: row.get(key, "")
                for key in fieldnames
            }
        )


# =========================================================
# Asset ZIP
# =========================================================

def download_image(url: str) -> bytes | None:
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        )

        if (
            "image" not in content_type
            and len(response.content) < 1000
        ):
            return None

        return response.content

    except Exception:
        return None


def create_zip(
    product_title: str,
    selected_images: list[dict],
    prompt_text: str,
    analysis: dict | None,
    selected_pair: dict | None,
    scene_card: dict | None,
) -> tuple[str, bytes]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(product_title)
    zip_name = f"{timestamp}_{base}_kling_assets.zip"
    zip_path = os.path.join(ZIP_DIR, zip_name)

    memory_zip = io.BytesIO()

    with zipfile.ZipFile(
        memory_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "kling_prompt.txt",
            prompt_text,
        )

        archive.writestr(
            "selected_images.csv",
            pd.DataFrame(
                selected_images
            ).to_csv(index=False),
        )

        if analysis:
            archive.writestr(
                "vision_analysis.json",
                json.dumps(
                    analysis,
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        if selected_pair:
            archive.writestr(
                "selected_pair.json",
                json.dumps(
                    selected_pair,
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        if scene_card:
            archive.writestr(
                "scene_card.json",
                json.dumps(
                    scene_card,
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            archive.writestr(
                "scene_card.md",
                scene_card_markdown(scene_card),
            )

        for index, image in enumerate(
            selected_images,
            start=1,
        ):
            image_bytes = download_image(
                image["url"]
            )

            if not image_bytes:
                continue

            extension = ".jpg"
            lowered = image["url"].lower().split("?")[0]

            for candidate_extension in [
                ".webp",
                ".png",
                ".jpeg",
                ".jpg",
            ]:
                if lowered.endswith(candidate_extension):
                    extension = candidate_extension
                    break

            archive.writestr(
                (
                    f"images/image_{index:02d}_source_"
                    f"{image.get('image_id')}{extension}"
                ),
                image_bytes,
            )

    memory_zip.seek(0)

    with open(zip_path, "wb") as file:
        file.write(memory_zip.getvalue())

    return zip_name, memory_zip.getvalue()


def create_storyflow_zip(
    product: dict,
    strategy: dict,
    storyboard: dict,
    image_map: dict,
    final_video: bytes | None,
    srt_text: str,
) -> tuple[str, bytes]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(product.get("title", "product"))
    zip_name = f"{timestamp}_{base}_storyflow.zip"

    memory_zip = io.BytesIO()

    with zipfile.ZipFile(
        memory_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "content_strategy.json",
            json.dumps(
                strategy,
                ensure_ascii=False,
                indent=2,
            ),
        )

        archive.writestr(
            "storyboard.json",
            json.dumps(
                storyboard,
                ensure_ascii=False,
                indent=2,
            ),
        )

        archive.writestr(
            "scene_image_map.json",
            json.dumps(
                image_map,
                ensure_ascii=False,
                indent=2,
            ),
        )

        selected_image_manifest = []
        for scene_number, recommendation in image_map.items():
            selected_image = selected_image_by_id(
                product.get("images", []),
                recommendation.get("image_id"),
            )
            selected_image_manifest.append(
                {
                    "scene_number": scene_number,
                    "image_id": recommendation.get("image_id"),
                    "image_score": recommendation.get("image_score"),
                    "reason_ko": recommendation.get("reason_ko"),
                    "image_url": selected_image.get("url") if selected_image else "",
                }
            )

        archive.writestr(
            "scene_selected_images.json",
            json.dumps(
                selected_image_manifest,
                ensure_ascii=False,
                indent=2,
            ),
        )

        archive.writestr(
            "captions_ja.srt",
            srt_text.encode("utf-8-sig"),
        )

        prompt_lines = []

        for scene in storyboard.get("scenes", []):
            prompt_lines.append(
                f"=== Scene {scene.get('scene_number')} ==="
            )
            prompt_lines.append(
                scene.get("kling_prompt", "")
            )
            prompt_lines.append("")
            prompt_lines.append(
                "Negative Prompt:"
            )
            prompt_lines.append(
                scene.get("negative_prompt", "")
            )
            prompt_lines.append("\n")

        archive.writestr(
            "kling_prompts.txt",
            "\n".join(prompt_lines),
        )

        if final_video:
            archive.writestr(
                "final_tiktok_15s.mp4",
                final_video,
            )

    memory_zip.seek(0)

    return zip_name, memory_zip.getvalue()


# =========================================================
# Analysis range helpers
# =========================================================

def default_analysis_start_index(product: dict) -> int:
    """Always start Vision analysis from the first extracted image."""
    return 1


def get_analysis_candidates(
    product: dict,
    start_number: int,
    max_count: int,
) -> list[dict]:
    images = product.get("images", [])
    start_index = max(
        0,
        int(start_number) - 1,
    )

    return images[
        start_index:start_index + max_count
    ]


def candidate_range_label(
    product: dict,
    start_number: int,
    max_count: int,
) -> str:
    total = len(product.get("images", []))

    if total == 0:
        return "분석 대상 이미지 없음"

    start = min(
        max(1, int(start_number)),
        total,
    )

    end = min(
        total,
        start + int(max_count) - 1,
    )

    return (
        f"사용 {start} ~ 사용 {end} / "
        f"전체 {total}장"
    )


def render_image_cards(
    images: list[dict],
    analysis: dict | None = None,
) -> None:
    if not images:
        st.warning(
            "추출된 이미지가 없습니다. 상품 페이지 HTML 구조를 확인해주세요."
        )
        return

    columns_per_row = 4

    for row_start in range(
        0,
        len(images),
        columns_per_row,
    ):
        columns = st.columns(columns_per_row)

        for offset, column in enumerate(columns):
            index = row_start + offset

            if index >= len(images):
                continue

            image = images[index]
            image_analysis = get_analysis_for_image(
                analysis or {},
                image.get("image_id"),
            )

            with column:
                st.image(
                    image["url"],
                    use_container_width=True,
                )

                st.markdown(
                    f"**사용 {image.get('image_id')}**"
                )

                if image_analysis:
                    score = int(
                        image_analysis.get(
                            "suitability_score"
                        )
                        or 0
                    )

                    image_type = image_analysis.get(
                        "image_type",
                        "unclear",
                    )

                    relevance = image_analysis.get(
                        "relevance_score"
                    )

                    global_flag = image_analysis.get(
                        "is_global_default"
                    )

                    extra = ""

                    if relevance is not None:
                        extra += (
                            f" / 관련도: {relevance}점"
                        )

                    if global_flag:
                        extra += (
                            " / 공통이미지 의심"
                        )

                    st.caption(
                        (
                            f"유형: "
                            f"{IMAGE_TYPE_LABELS.get(image_type, image_type)} "
                            f"/ 적합도: {score}점{extra}"
                        )
                    )

                    st.caption(
                        (
                            "역할: "
                            f"{image_analysis.get('recommended_role', '-')}"
                        )
                    )

                    st.caption(
                        image_analysis.get(
                            "short_reason_ko",
                            "",
                        )
                    )

                    if image_analysis.get(
                        "cautions_ko"
                    ):
                        st.warning(
                            image_analysis.get(
                                "cautions_ko"
                            ),
                            icon="⚠️",
                        )

                else:
                    st.caption(
                        (
                            f"{image.get('source', '')} / "
                            f"{normalize_space(image.get('alt', ''))[:45]}"
                        )
                    )


# =========================================================
# FFmpeg and SRT
# =========================================================

def run_subprocess(command: list[str]) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or "FFmpeg 실행에 실패했습니다."
        )


def normalize_video_clip(
    input_path: str,
    output_path: str,
    duration: int = 5,
) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-t",
        str(duration),
        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30,"
            "setsar=1"
        ),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ]

    run_subprocess(command)


def concatenate_video_clips(
    input_paths: list[str],
    output_path: str,
) -> None:
    if not input_paths:
        raise ValueError(
            "연결할 영상이 없습니다."
        )

    list_path = os.path.join(
        os.path.dirname(output_path),
        "concat_list.txt",
    )

    with open(
        list_path,
        "w",
        encoding="utf-8",
    ) as file:
        for path in input_paths:
            absolute_path = os.path.abspath(path)
            escaped_path = absolute_path.replace(
                "'",
                "'\\''",
            )
            file.write(
                f"file '{escaped_path}'\n"
            )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]

    run_subprocess(command)


def create_final_video(
    uploaded_clips: list[dict],
) -> bytes:
    if len(uploaded_clips) != 3:
        raise ValueError(
            "Scene 1~3 영상이 모두 필요합니다."
        )

    uploaded_clips = sorted(
        uploaded_clips,
        key=lambda item: item["scene_number"],
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        normalized_paths = []

        for item in uploaded_clips:
            scene_number = item["scene_number"]
            uploaded_file = item["file"]

            suffix = os.path.splitext(
                uploaded_file.name
            )[1] or ".mp4"

            input_path = os.path.join(
                temp_dir,
                f"scene_{scene_number}_input{suffix}",
            )

            normalized_path = os.path.join(
                temp_dir,
                f"scene_{scene_number}_normalized.mp4",
            )

            with open(input_path, "wb") as file:
                file.write(
                    uploaded_file.getbuffer()
                )

            normalize_video_clip(
                input_path=input_path,
                output_path=normalized_path,
                duration=5,
            )

            normalized_paths.append(
                normalized_path
            )

        final_path = os.path.join(
            temp_dir,
            "final_tiktok_video.mp4",
        )

        concatenate_video_clips(
            normalized_paths,
            final_path,
        )

        with open(final_path, "rb") as file:
            return file.read()


def srt_timestamp(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},000"
    )


def create_storyboard_srt(
    storyboard: dict,
) -> str:
    blocks = []

    for index, scene in enumerate(
        storyboard.get("scenes", []),
        start=1,
    ):
        start_seconds = (index - 1) * 5
        end_seconds = index * 5

        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{srt_timestamp(start_seconds)} --> "
                        f"{srt_timestamp(end_seconds)}"
                    ),
                    scene.get(
                        "caption_ja",
                        "",
                    ),
                ]
            )
        )

    return "\n\n".join(blocks)


# =========================================================
# UI renderers
# =========================================================

def render_product_summary(
    product: dict,
) -> None:
    st.subheader("1. 상품 정보")

    col1, col2, col3 = st.columns(
        [2, 2, 1]
    )

    with col1:
        st.write("**상품명**")
        st.write(
            product.get("title") or "-"
        )

    with col2:
        st.write("**설명 요약**")
        st.write(
            (
                product.get("description")
                or "-"
            )[:250]
        )

    with col3:
        st.metric(
            "추출 이미지",
            len(
                product.get(
                    "images",
                    [],
                )
            ),
        )

    if product.get("price"):
        st.caption(
            f"가격 정보: {product.get('price')}"
        )


def render_pair_recommendations(
    product: dict,
    analysis: dict,
) -> tuple[dict | None, list[dict]]:
    st.subheader("5. 추천 Pair TOP 3")

    if analysis.get(
        "overall_notes_ko"
    ):
        st.info(
            analysis.get(
                "overall_notes_ko"
            )
        )

    pairs = analysis.get(
        "pair_recommendations",
        [],
    )

    if not pairs:
        st.warning(
            "추천 Pair가 없습니다."
        )
        return None, []

    for pair in pairs:
        with st.container(border=True):
            st.markdown(
                f"**{pair_label(pair)}**"
            )

            columns = st.columns(
                [1, 1, 3]
            )

            images = selected_images_from_pair(
                product.get(
                    "images",
                    [],
                ),
                pair,
            )

            with columns[0]:
                if len(images) > 0:
                    st.image(
                        images[0]["url"],
                        caption=(
                            "Start: 사용 "
                            f"{pair.get('start_image_id')}"
                        ),
                        use_container_width=True,
                    )

            with columns[1]:
                if len(images) > 1:
                    st.image(
                        images[1]["url"],
                        caption=(
                            "End: 사용 "
                            f"{pair.get('end_image_id')}"
                        ),
                        use_container_width=True,
                    )

            with columns[2]:
                st.write(
                    pair.get(
                        "reason_ko",
                        "",
                    )
                )

                if pair.get(
                    "cautions_ko"
                ):
                    st.warning(
                        pair.get(
                            "cautions_ko"
                        ),
                        icon="⚠️",
                    )

                st.caption(
                    (
                        "추천 모션: "
                        f"{pair.get('suggested_motion', '-')}"
                    )
                )

    pair_options = {
        pair_label(pair): pair
        for pair in pairs
    }

    chosen_label = st.radio(
        "사용할 추천 조합 선택",
        list(pair_options.keys()),
        index=0,
        key="mini_pair_radio",
    )

    selected_pair = pair_options[
        chosen_label
    ]

    selected_images = selected_images_from_pair(
        product.get(
            "images",
            [],
        ),
        selected_pair,
    )

    return selected_pair, selected_images


def render_storyboard_scene(
    scene: dict,
    product: dict,
    pair_options: dict,
    scene_index: int,
) -> dict | None:
    scene_number = scene.get(
        "scene_number",
        scene_index + 1,
    )

    with st.container(border=True):
        st.markdown(
            (
                f"### Scene {scene_number} "
                f"· {scene.get('role', '-')}"
            )
        )

        st.caption(
            f"{(scene_number - 1) * 5}~{scene_number * 5}초"
        )

        st.write(
            "**일본어 자막**"
        )

        updated_caption = st.text_input(
            "caption",
            value=scene.get(
                "caption_ja",
                "",
            ),
            key=f"scene_caption_{scene_number}",
            label_visibility="collapsed",
        )

        scene["caption_ja"] = updated_caption

        st.write("**Visual**")
        st.write(
            scene.get(
                "visual_description",
                "-",
            )
        )

        st.write("**Action**")
        st.write(
            scene.get(
                "action",
                "-",
            )
        )

        st.write("**Camera**")
        st.write(
            scene.get(
                "camera",
                "-",
            )
        )

        c1, c2 = st.columns(2)

        with c1:
            st.write("**Start State**")
            st.write(
                scene.get(
                    "starting_state",
                    "-",
                )
            )

        with c2:
            st.write("**End State**")
            st.write(
                scene.get(
                    "ending_state",
                    "-",
                )
            )

        st.write(
            "**Continuity**"
        )

        st.write(
            scene.get(
                "continuity_instruction",
                "-",
            )
        )

        selected_pair = None

        if pair_options:
            default_index = min(
                scene_index,
                len(pair_options) - 1,
            )

            chosen_label = st.selectbox(
                "이 Scene에 사용할 이미지 Pair",
                options=list(
                    pair_options.keys()
                ),
                index=default_index,
                key=f"scene_pair_{scene_number}",
            )

            selected_pair = pair_options[
                chosen_label
            ]

            selected_images = selected_images_from_pair(
                product.get(
                    "images",
                    [],
                ),
                selected_pair,
            )

            image_cols = st.columns(2)

            with image_cols[0]:
                if selected_images:
                    st.image(
                        selected_images[0]["url"],
                        caption=(
                            "Start: 사용 "
                            f"{selected_pair.get('start_image_id')}"
                        ),
                        use_container_width=True,
                    )

            with image_cols[1]:
                if len(selected_images) > 1:
                    st.image(
                        selected_images[1]["url"],
                        caption=(
                            "End: 사용 "
                            f"{selected_pair.get('end_image_id')}"
                        ),
                        use_container_width=True,
                    )

        st.write(
            "**Kling Prompt**"
        )

        updated_prompt = st.text_area(
            "kling prompt",
            value=scene.get(
                "kling_prompt",
                "",
            ),
            height=220,
            key=f"scene_prompt_{scene_number}",
            label_visibility="collapsed",
        )

        scene["kling_prompt"] = updated_prompt

        st.write(
            "**Negative Prompt**"
        )

        updated_negative = st.text_area(
            "negative prompt",
            value=scene.get(
                "negative_prompt",
                "",
            ),
            height=120,
            key=f"scene_negative_{scene_number}",
            label_visibility="collapsed",
        )

        scene["negative_prompt"] = (
            updated_negative
        )

        if selected_pair:
            scene[
                "start_image_id"
            ] = selected_pair.get(
                "start_image_id"
            )
            scene[
                "end_image_id"
            ] = selected_pair.get(
                "end_image_id"
            )
            scene[
                "pair_score"
            ] = selected_pair.get(
                "pair_score"
            )

        return selected_pair



def render_storyboard_scene_single_image(
    scene: dict,
    product: dict,
    image_options: dict,
    scene_index: int,
) -> dict | None:
    """Render a StoryFlow scene using one Kling reference image."""
    scene_number = int(scene.get("scene_number") or scene_index + 1)

    with st.container(border=True):
        st.markdown(f"### Scene {scene_number} · {scene.get('role', '-')}")
        st.caption(f"{(scene_number - 1) * 5}~{scene_number * 5}초")

        scene["caption_ja"] = st.text_input(
            "caption",
            value=scene.get("caption_ja", ""),
            key=f"scene_caption_{scene_number}",
            label_visibility="collapsed",
        )

        st.write("**Visual**")
        st.write(scene.get("visual_description", "-"))
        st.write("**Action**")
        st.write(scene.get("action", "-"))
        st.write("**Camera**")
        st.write(scene.get("camera", "-"))

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Start State**")
            st.write(scene.get("starting_state", "-"))
        with c2:
            st.write("**End State**")
            st.write(scene.get("ending_state", "-"))

        st.write("**Continuity**")
        st.write(scene.get("continuity_instruction", "-"))

        selected_recommendation = None
        if image_options:
            labels = list(image_options.keys())
            preferred_image_id = str(scene.get("reference_image_id", ""))
            default_index = 0
            for index, label in enumerate(labels):
                if str(image_options[label].get("image_id")) == preferred_image_id:
                    default_index = index
                    break

            chosen_label = st.selectbox(
                "이 Scene에 사용할 기준 이미지 1장",
                options=labels,
                index=default_index,
                key=f"scene_single_image_{scene_number}",
            )
            selected_recommendation = image_options[chosen_label]
            selected_image = selected_image_by_id(
                product.get("images", []),
                selected_recommendation.get("image_id"),
            )

            st.markdown("#### Kling에 넣을 기준 이미지")
            image_meta_1, image_meta_2 = st.columns(2)
            with image_meta_1:
                st.metric(
                    "선택 이미지 번호",
                    f"사용 {selected_recommendation.get('image_id')}",
                )
            with image_meta_2:
                st.metric(
                    "AI 적합도",
                    f"{selected_recommendation.get('image_score', '-')}점",
                )

            if selected_image:
                st.image(
                    selected_image["url"],
                    caption=(
                        f"Scene {scene_number} 기준 이미지 · "
                        f"사용 {selected_recommendation.get('image_id')}"
                    ),
                    use_container_width=True,
                )
                st.code(selected_image["url"], language=None)
            else:
                st.error(
                    f"선택된 이미지 ID {selected_recommendation.get('image_id')}를 "
                    "상품 이미지 목록에서 찾지 못했습니다."
                )

            st.info(
                "선정 이유: "
                + (selected_recommendation.get("reason_ko") or "-")
            )
            if selected_recommendation.get("cautions_ko"):
                st.warning(selected_recommendation.get("cautions_ko"), icon="⚠️")

            scene["reference_image_id"] = selected_recommendation.get("image_id")
            scene["image_score"] = selected_recommendation.get("image_score")
            scene["suggested_motion"] = selected_recommendation.get("suggested_motion")

        st.write("**Kling Prompt**")
        scene["kling_prompt"] = st.text_area(
            "kling prompt",
            value=scene.get("kling_prompt", ""),
            height=220,
            key=f"scene_prompt_{scene_number}",
            label_visibility="collapsed",
        )

        st.write("**Negative Prompt**")
        scene["negative_prompt"] = st.text_area(
            "negative prompt",
            value=scene.get("negative_prompt", ""),
            height=120,
            key=f"scene_negative_{scene_number}",
            label_visibility="collapsed",
        )

        return selected_recommendation

def render_clip_uploads(
    storyboard: dict,
) -> list[dict]:
    uploaded_clips = []

    for scene in storyboard.get(
        "scenes",
        [],
    ):
        scene_number = scene.get(
            "scene_number"
        )

        with st.container(border=True):
            st.markdown(
                f"### Scene {scene_number}"
            )

            st.write(
                scene.get(
                    "caption_ja",
                    "",
                )
            )

            st.code(
                scene.get(
                    "kling_prompt",
                    "",
                ),
                language=None,
            )

            uploaded = st.file_uploader(
                (
                    f"Scene {scene_number} "
                    "Kling 영상 업로드"
                ),
                type=["mp4", "mov", "m4v"],
                key=f"scene_clip_{scene_number}",
            )

            if uploaded:
                st.video(uploaded)

                uploaded_clips.append(
                    {
                        "scene_number": (
                            scene_number
                        ),
                        "file": uploaded,
                    }
                )

    return uploaded_clips


# =========================================================
# Main workflow sections
# =========================================================

def render_shared_image_analysis(
    product: dict,
    category: str,
    model: str,
    api_key: str,
    max_vision_images: int,
    video_type: str,
    video_strategy: str,
) -> tuple[dict | None, list[dict]]:
    st.subheader(
        "3. 이미지 필터 / 분석 대상 선택"
    )

    all_images = product.get(
        "images",
        [],
    )

    default_start = default_analysis_start_index(
        product
    )

    if (
        "analysis_start_number"
        not in st.session_state
    ):
        st.session_state[
            "analysis_start_number"
        ] = default_start

    c1, c2, c3 = st.columns(
        [1, 1, 2]
    )

    with c1:
        analysis_start_number = st.number_input(
            "분석 시작 이미지 번호",
            min_value=1,
            max_value=max(
                1,
                len(all_images),
            ),
            value=min(
                st.session_state.get(
                    "analysis_start_number",
                    default_start,
                ),
                max(
                    1,
                    len(all_images),
                ),
            ),
            step=1,
            help=(
                "앞부분에 공통/디폴트 이미지가 섞이면 "
                "실제 상품 이미지 시작 번호로 조정하세요."
            ),
        )

        st.session_state[
            "analysis_start_number"
        ] = analysis_start_number

    with c2:
        st.metric(
            "분석 대상",
            candidate_range_label(
                product,
                analysis_start_number,
                max_vision_images,
            ),
        )

    with c3:
        st.info(
            (
                "앞부분 이미지가 다른 상품으로 섞여 있으면 "
                "실제 상품 이미지가 시작되는 번호로 변경하세요."
            )
        )

    analysis_candidates = get_analysis_candidates(
        product,
        analysis_start_number,
        max_vision_images,
    )

    st.caption(
        (
            f"전체 {len(all_images)}장 중 "
            f"GPT Vision 분석 대상 "
            f"{len(analysis_candidates)}장"
        )
    )

    render_image_cards(
        analysis_candidates,
        st.session_state.get(
            "vision_analysis"
        ),
    )

    with st.expander(
        "전체 추출 이미지 보기",
        expanded=False,
    ):
        render_image_cards(
            all_images,
            None,
        )

    st.subheader(
        "4. GPT Vision 이미지 분석"
    )

    analyze_clicked = st.button(
        "GPT Vision으로 이미지 분석",
        type="primary",
        use_container_width=True,
        key="vision_analyze_button",
    )

    if analyze_clicked:
        if not api_key.strip():
            st.error(
                "OpenAI API Key를 입력해주세요."
            )

        elif len(analysis_candidates) < 2:
            st.error(
                "Pair 추천에는 최소 2장의 이미지가 필요합니다."
            )

        else:
            with st.spinner(
                (
                    "GPT Vision이 이미지를 분석하고 "
                    "추천 조합을 만드는 중입니다..."
                )
            ):
                try:
                    analysis = analyze_images_and_pairs(
                        product,
                        analysis_candidates,
                        category,
                        video_type,
                        video_strategy,
                        model,
                        api_key,
                    )

                    analysis = normalize_pair_recommendations(
                        analysis_candidates,
                        analysis,
                        video_strategy,
                    )
                    analysis = normalize_scene_image_recommendations(
                        analysis_candidates,
                        analysis,
                        scene_count=3,
                    )

                    st.session_state[
                        "vision_analysis"
                    ] = analysis

                    st.success(
                        "이미지 분석 완료"
                    )

                    st.rerun()

                except Exception as exc:
                    st.error(
                        f"Vision 분석 실패: {exc}"
                    )

    return (
        st.session_state.get(
            "vision_analysis"
        ),
        analysis_candidates,
    )


def render_mini_shotflow(
    product: dict,
    category: str,
    model: str,
    api_key: str,
    max_vision_images: int,
) -> None:
    st.subheader(
        "2. 단일 영상 용도 / Product Lock"
    )

    video_type = st.selectbox(
        "영상 용도",
        list(
            VIDEO_TYPE_OPTIONS.keys()
        ),
        index=0,
    )

    video_strategy = st.selectbox(
        "추천 전략",
        list(
            VIDEO_STRATEGY_OPTIONS.keys()
        ),
        index=0,
    )

    st.info(
        VIDEO_STRATEGY_OPTIONS.get(
            video_strategy,
            {},
        ).get("summary", "")
    )

    settings = video_type_settings(
        video_type
    )

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "기본 비율",
        settings.get("ratio"),
    )
    c2.metric(
        "기본 길이",
        settings.get("duration"),
    )

    with c3:
        st.write("**목표**")
        st.write(
            settings.get("goal")
        )

    with st.expander(
        "Product / Audio Lock 보기"
    ):
        for rule in product_lock_rules(
            category
        ):
            st.write(
                f"- {rule}"
            )

        for rule in audio_lock_rules():
            st.write(
                f"- {rule}"
            )

    analysis, _ = render_shared_image_analysis(
        product=product,
        category=category,
        model=model,
        api_key=api_key,
        max_vision_images=max_vision_images,
        video_type=video_type,
        video_strategy=video_strategy,
    )

    if not analysis:
        return

    selected_pair, selected_images = (
        render_pair_recommendations(
            product,
            analysis,
        )
    )

    if not selected_images:
        return

    st.subheader(
        "6. Scene Card / Kling 프롬프트 조건"
    )

    c1, c2, c3 = st.columns(3)

    with c1:
        style = st.selectbox(
            "영상 스타일",
            STYLE_OPTIONS,
            index=0,
        )

    with c2:
        default_ratio = settings.get(
            "ratio",
            "1:1",
        )

        ratio = st.selectbox(
            "추천 비율",
            RATIO_OPTIONS,
            index=(
                RATIO_OPTIONS.index(
                    default_ratio
                )
                if default_ratio
                in RATIO_OPTIONS
                else 0
            ),
        )

    with c3:
        default_duration = settings.get(
            "duration",
            "5 seconds",
        )

        duration = st.selectbox(
            "영상 길이",
            DURATION_OPTIONS,
            index=(
                DURATION_OPTIONS.index(
                    default_duration
                )
                if default_duration
                in DURATION_OPTIONS
                else 0
            ),
        )

    scene_card = build_scene_card(
        product,
        category,
        video_type,
        video_strategy,
        style,
        ratio,
        duration,
        selected_pair,
        selected_images,
    )

    st.markdown(
        scene_card_markdown(
            scene_card
        )
    )

    st.session_state[
        "scene_card"
    ] = scene_card

    generate_clicked = st.button(
        "Scene Card 기반 Kling용 프롬프트 생성",
        type="primary",
        use_container_width=True,
    )

    if generate_clicked:
        with st.spinner(
            "GPT가 Kling용 프롬프트를 생성하는 중입니다..."
        ):
            request_text = build_prompt_request(
                product,
                selected_images,
                category,
                video_type,
                video_strategy,
                style,
                ratio,
                duration,
                selected_pair,
                analysis,
                scene_card,
            )

            try:
                result = generate_prompt_with_openai(
                    request_text,
                    model,
                    api_key,
                )
            except Exception as exc:
                result = (
                    f"프롬프트 생성 실패: {exc}\n\n"
                    + fallback_prompt()
                )

            st.session_state[
                "generated_prompt"
            ] = result

            st.session_state[
                "selected_images"
            ] = selected_images

            st.session_state[
                "selected_pair"
            ] = selected_pair

            st.session_state[
                "prompt_meta"
            ] = {
                "category": category,
                "video_type": video_type,
                "video_strategy": (
                    video_strategy
                ),
                "style": style,
                "ratio": ratio,
                "duration": duration,
                "model": model,
            }

    generated = st.session_state.get(
        "generated_prompt"
    )

    if not generated:
        return

    st.divider()
    st.subheader(
        "7. 생성된 Kling 프롬프트"
    )

    st.text_area(
        "복사해서 Kling에 붙여넣기",
        value=generated,
        height=420,
    )

    d1, d2, d3 = st.columns(3)

    with d1:
        if st.button(
            "작업 로그 저장",
            use_container_width=True,
        ):
            meta = st.session_state.get(
                "prompt_meta",
                {},
            )

            current_pair = (
                st.session_state.get(
                    "selected_pair",
                    {},
                )
            )

            append_log(
                {
                    "created_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "product_url": product.get(
                        "url"
                    ),
                    "product_title": product.get(
                        "title"
                    ),
                    "category": meta.get(
                        "category"
                    ),
                    "video_type": meta.get(
                        "video_type"
                    ),
                    "video_strategy": meta.get(
                        "video_strategy"
                    ),
                    "style": meta.get(
                        "style"
                    ),
                    "ratio": meta.get(
                        "ratio"
                    ),
                    "duration": meta.get(
                        "duration"
                    ),
                    "selected_image_ids": ",".join(
                        str(
                            image.get(
                                "image_id"
                            )
                        )
                        for image in st.session_state.get(
                            "selected_images",
                            [],
                        )
                    ),
                    "selected_pair_score": (
                        current_pair.get(
                            "pair_score",
                            "",
                        )
                    ),
                    "model": meta.get(
                        "model"
                    ),
                    "prompt_hash": hashlib.md5(
                        generated.encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                    "video_created": "",
                    "shopify_uploaded": "",
                    "memo": "",
                }
            )

            st.success(
                f"로그 저장 완료: {LOG_FILE}"
            )

    with d2:
        zip_name, zip_bytes = create_zip(
            product.get(
                "title",
                "product",
            ),
            st.session_state.get(
                "selected_images",
                [],
            ),
            generated,
            analysis,
            selected_pair,
            scene_card,
        )

        st.download_button(
            "선택 이미지 + 프롬프트 ZIP",
            data=zip_bytes,
            file_name=zip_name,
            mime="application/zip",
            use_container_width=True,
        )

    with d3:
        if os.path.exists(
            LOG_FILE
        ):
            with open(
                LOG_FILE,
                "rb",
            ) as file:
                st.download_button(
                    "CSV 로그 다운로드",
                    data=file.read(),
                    file_name=(
                        "generation_log.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                )

    st.subheader(
        "8. Kling Take Log / 재생성 프롬프트"
    )

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)

        with c1:
            take_no = st.number_input(
                "Take 번호",
                min_value=1,
                value=1,
                step=1,
            )

            take_status = st.selectbox(
                "상태",
                TAKE_STATUS_OPTIONS,
                index=1,
            )

        with c2:
            final_rating = st.slider(
                "결과 평가",
                min_value=0,
                max_value=5,
                value=0,
            )

            shopify_uploaded = st.checkbox(
                "Shopify 등록 완료"
            )

        with c3:
            kling_result_url = st.text_input(
                "Kling 결과 URL/파일명",
                placeholder="선택 입력",
            )

        issue_types = st.multiselect(
            "문제 유형",
            ISSUE_OPTIONS,
        )

        take_memo = st.text_area(
            "메모",
            placeholder=(
                "예: 제품은 안정적이나 배경이 복잡함"
            ),
        )

        c1, c2 = st.columns(2)

        with c1:
            if st.button(
                "Take Log 저장",
                use_container_width=True,
            ):
                meta = st.session_state.get(
                    "prompt_meta",
                    {},
                )

                append_take_log(
                    {
                        "created_at": datetime.now().isoformat(
                            timespec="seconds"
                        ),
                        "product_url": product.get(
                            "url"
                        ),
                        "product_title": product.get(
                            "title"
                        ),
                        "video_type": meta.get(
                            "video_type"
                        ),
                        "take_no": take_no,
                        "status": take_status,
                        "issue_types": ", ".join(
                            issue_types
                        ),
                        "final_rating": final_rating,
                        "kling_result_url": (
                            kling_result_url
                        ),
                        "shopify_uploaded": (
                            "yes"
                            if shopify_uploaded
                            else ""
                        ),
                        "memo": take_memo,
                    }
                )

                st.success(
                    f"Take Log 저장 완료: {TAKE_LOG_FILE}"
                )

        with c2:
            if os.path.exists(
                TAKE_LOG_FILE
            ):
                with open(
                    TAKE_LOG_FILE,
                    "rb",
                ) as file:
                    st.download_button(
                        "Take Log CSV",
                        data=file.read(),
                        file_name=(
                            "take_log.csv"
                        ),
                        mime="text/csv",
                        use_container_width=True,
                    )

        if st.button(
            "문제 기반 재생성 프롬프트 만들기",
            use_container_width=True,
        ):
            request_text = build_regeneration_request(
                product,
                scene_card,
                issue_types,
                take_memo,
                generated,
            )

            try:
                regeneration = (
                    generate_regeneration_instruction(
                        request_text,
                        model,
                        api_key,
                    )
                )
            except Exception as exc:
                regeneration = (
                    f"재생성 프롬프트 생성 실패: {exc}"
                )

            st.session_state[
                "regen_prompt"
            ] = regeneration

        if st.session_state.get(
            "regen_prompt"
        ):
            st.text_area(
                "Kling 재생성용 추가 지시문",
                value=st.session_state.get(
                    "regen_prompt"
                ),
                height=180,
            )


def render_storyflow(
    product: dict,
    category: str,
    model: str,
    api_key: str,
    max_vision_images: int,
) -> None:
    st.subheader(
        "2. TikTok StoryFlow 기본 설정"
    )

    video_type = "SNS Short Video"
    video_strategy = (
        "Lifestyle First / 착샷 우선"
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "플랫폼",
        "TikTok Shop",
    )

    c2.metric(
        "비율",
        "9:16",
    )

    c3.metric(
        "구성",
        "5초 × 3 Scene",
    )

    st.info(
        (
            "콘텐츠 테마, 타깃, 신발, 스타일, 판매 포인트, CTA는 "
            "상품 정보와 이미지 분석을 바탕으로 AI가 자동 판단합니다."
        )
    )

    with st.expander(
        "StoryFlow Product / Audio Lock 보기"
    ):
        for rule in product_lock_rules(
            category
        ):
            st.write(
                f"- {rule}"
            )

        for rule in audio_lock_rules():
            st.write(
                f"- {rule}"
            )

    analysis, _ = render_shared_image_analysis(
        product=product,
        category=category,
        model=model,
        api_key=api_key,
        max_vision_images=max_vision_images,
        video_type=video_type,
        video_strategy=video_strategy,
    )

    if not analysis:
        st.info(
            (
                "먼저 GPT Vision 분석을 실행한 뒤 "
                "AI 콘텐츠 전략을 생성해주세요."
            )
        )
        return

    st.subheader(
        "5. AI 콘텐츠 전략"
    )

    if st.button(
        "AI가 콘텐츠 전략 판단",
        type="primary",
        use_container_width=True,
        key="generate_strategy_button",
    ):
        try:
            with st.spinner(
                "상품과 이미지 자산을 분석해 TikTok 전략을 결정하는 중입니다..."
            ):
                strategy = generate_content_strategy(
                    product,
                    category,
                    analysis,
                    model,
                    api_key,
                )

                st.session_state[
                    "content_strategy"
                ] = strategy

                st.session_state.pop(
                    "storyboard",
                    None,
                )

                st.session_state.pop(
                    "final_video",
                    None,
                )

                st.success(
                    "AI 콘텐츠 전략 생성 완료"
                )

                st.rerun()

        except Exception as exc:
            st.error(
                f"전략 생성 실패: {exc}"
            )

    strategy = st.session_state.get(
        "content_strategy"
    )

    if not strategy:
        return

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Theme",
            strategy.get(
                "content_theme",
                "-",
            ),
        )

        c2.metric(
            "Target",
            strategy.get(
                "target_audience",
                "-",
            ),
        )

        c3.metric(
            "Shoes",
            strategy.get(
                "recommended_shoes",
                "-",
            ),
        )

        st.write(
            "**Visual Style:**",
            strategy.get(
                "visual_style",
                "-",
            ),
        )

        st.write(
            "**Main Selling Point:**",
            strategy.get(
                "primary_selling_point",
                "-",
            ),
        )

        st.write(
            "**Subtitle Tone:**",
            strategy.get(
                "subtitle_tone",
                "-",
            ),
        )

        st.write(
            "**CTA:**",
            strategy.get(
                "cta_ja",
                "-",
            ),
        )

        st.write(
            "**Audio Policy:**",
            strategy.get(
                "audio_policy",
                "silent",
            ),
        )

        st.info(
            strategy.get(
                "recommendation_reason_ko",
                "",
            )
        )

    if st.button(
        "이 전략으로 15초 Storyboard 생성",
        type="primary",
        use_container_width=True,
        key="generate_storyboard_button",
    ):
        try:
            with st.spinner(
                "3개 Scene Storyboard를 생성하는 중입니다..."
            ):
                storyboard = generate_storyboard(
                    product,
                    category,
                    strategy,
                    analysis,
                    model,
                    api_key,
                )

                storyboard = assign_default_images_to_storyboard(
                    storyboard,
                    analysis.get(
                        "scene_image_recommendations",
                        [],
                    ),
                )

                st.session_state[
                    "storyboard"
                ] = storyboard

                st.session_state.pop(
                    "final_video",
                    None,
                )

                st.success(
                    "Storyboard 생성 완료"
                )

                st.rerun()

        except Exception as exc:
            st.error(
                f"Storyboard 생성 실패: {exc}"
            )

    storyboard = st.session_state.get(
        "storyboard"
    )

    if not storyboard:
        return

    st.subheader(
        "6. Storyboard Editor"
    )

    st.markdown(
        f"### {storyboard.get('content_title_ja', '')}"
    )

    st.write(
        storyboard.get(
            "content_summary_ko",
            "",
        )
    )

    # Older Streamlit sessions may contain Vision results created before
    # scene-level single-image recommendations were introduced. Normalize
    # them again here so the Storyboard Editor always has visible images.
    analysis = normalize_scene_image_recommendations(
        product.get("images", []),
        analysis,
        scene_count=3,
    )
    st.session_state["vision_analysis"] = analysis

    scene_image_recommendations = analysis.get(
        "scene_image_recommendations",
        [],
    )

    image_options = {
        scene_image_label(item): item
        for item in scene_image_recommendations
    }

    st.markdown("### Scene별 AI 추천 이미지")
    if scene_image_recommendations:
        preview_columns = st.columns(3)
        for index, recommendation in enumerate(scene_image_recommendations[:3]):
            scene_number = int(recommendation.get("scene_number") or index + 1)
            selected_image = selected_image_by_id(
                product.get("images", []),
                recommendation.get("image_id"),
            )
            with preview_columns[index]:
                st.markdown(f"**Scene {scene_number}**")
                if selected_image:
                    st.image(
                        selected_image["url"],
                        caption=(
                            f"사용 {recommendation.get('image_id')} · "
                            f"{recommendation.get('image_score', '-')}점"
                        ),
                        use_container_width=True,
                    )
                else:
                    st.warning(
                        f"이미지 {recommendation.get('image_id')}를 찾지 못했습니다."
                    )
                st.caption(recommendation.get("reason_ko", ""))
    else:
        st.error(
            "상품 페이지에서 사용할 수 있는 이미지가 없어 Scene 이미지를 배정하지 못했습니다. "
            "전체 추출 이미지와 분석 시작 번호를 확인해주세요."
        )

    scene_image_map = {}

    for index, scene in enumerate(
        storyboard.get(
            "scenes",
            [],
        )
    ):
        selected_recommendation = render_storyboard_scene_single_image(
            scene,
            product,
            image_options,
            index,
        )

        if selected_recommendation:
            scene_image_map[
                str(scene.get("scene_number"))
            ] = selected_recommendation

    st.session_state["storyboard"] = storyboard
    st.session_state["scene_image_map"] = scene_image_map

    st.subheader(
        "7. Kling 영상 업로드"
    )

    st.caption(
        (
            "각 Scene에 추천된 기준 이미지 1장과 프롬프트를 Kling에 넣고 5초 무음 영상을 생성한 뒤 "
            "Scene 1~3 순서대로 업로드하세요."
        )
    )

    uploaded_clips = render_clip_uploads(
        storyboard
    )

    st.subheader(
        "8. FFmpeg 영상 결합"
    )

    if not check_ffmpeg():
        st.error(
            (
                "현재 실행 환경에서 FFmpeg를 찾을 수 없습니다. "
                "로컬 또는 배포 환경에 FFmpeg를 설치해주세요."
            )
        )

    elif len(uploaded_clips) < 3:
        st.info(
            (
                f"현재 {len(uploaded_clips)}/3개 업로드됨. "
                "Scene 1~3 영상이 모두 필요합니다."
            )
        )

    else:
        if st.button(
            "3개 Kling 영상을 15초로 합치기",
            type="primary",
            use_container_width=True,
            key="combine_video_button",
        ):
            try:
                with st.spinner(
                    "영상을 9:16 / 1080×1920 / 30fps로 통일하고 연결하는 중입니다..."
                ):
                    final_video = create_final_video(
                        uploaded_clips
                    )

                    st.session_state[
                        "final_video"
                    ] = final_video

                    st.success(
                        "15초 영상 생성 완료"
                    )

            except Exception as exc:
                st.error(
                    f"영상 결합 실패: {exc}"
                )

    final_video = st.session_state.get(
        "final_video"
    )

    srt_text = create_storyboard_srt(
        storyboard
    )

    st.subheader(
        "9. 다운로드"
    )

    if final_video:
        st.video(
            final_video
        )

        st.download_button(
            "완성 MP4 다운로드",
            data=final_video,
            file_name=(
                f"{safe_filename(product.get('title', 'product'))}"
                "_tiktok_15s.mp4"
            ),
            mime="video/mp4",
            use_container_width=True,
        )

    st.download_button(
        "CapCut용 일본어 SRT 다운로드",
        data=srt_text.encode(
            "utf-8-sig"
        ),
        file_name="captions_ja.srt",
        mime="application/x-subrip",
        use_container_width=True,
    )

    st.download_button(
        "Storyboard JSON 다운로드",
        data=json.dumps(
            storyboard,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        file_name="storyboard.json",
        mime="application/json",
        use_container_width=True,
    )

    zip_name, zip_bytes = create_storyflow_zip(
        product=product,
        strategy=strategy,
        storyboard=storyboard,
        image_map=scene_image_map,
        final_video=final_video,
        srt_text=srt_text,
    )

    st.download_button(
        "StoryFlow 전체 패키지 ZIP",
        data=zip_bytes,
        file_name=zip_name,
        mime="application/zip",
        use_container_width=True,
    )

    st.subheader(
        "10. CapCut 마무리 체크리스트"
    )

    st.markdown(
        """
- 완성 MP4를 CapCut에 불러옵니다.
- `captions_ja.srt`를 불러오거나 자막을 직접 적용합니다.
- TikTok에서 사용 가능한 트렌드 음원을 추가합니다.
- Scene 전환 지점은 우선 Hard Cut을 유지하고 필요할 때만 짧은 전환 효과를 적용합니다.
- 자막은 TikTok 상품카드 및 UI와 겹치지 않도록 화면 중앙보다 약간 아래에 배치합니다.
- 실제 상품의 색상, 패턴, 길이, 형태가 달라진 Scene은 사용하지 않습니다.
        """.strip()
    )


# =========================================================
# Main
# =========================================================

def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🧦",
        layout="wide",
    )

    st.title(
        "🧦 SocksLover AI Content Engine"
    )

    st.caption(
        (
            f"{APP_VERSION} · Shopify 상품 분석부터 "
            "TikTok Storyboard, 무음 Kling Prompt, "
            "FFmpeg 영상 결합까지"
        )
    )

    with st.sidebar:
        st.header("설정")

        workflow = st.radio(
            "Workflow",
            WORKFLOW_OPTIONS,
            index=1,
        )

        model = st.text_input(
            "OpenAI model",
            value=DEFAULT_MODEL,
        )

        api_key = st.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="sk-...",
            help=(
                "현재 Streamlit 세션에서만 사용되며 "
                "로그와 ZIP에는 저장되지 않습니다."
            ),
        )

        max_vision_images = st.slider(
            "Vision 분석 최대 이미지 수",
            min_value=4,
            max_value=30,
            value=12,
            step=1,
        )

        st.divider()

        st.markdown(
            "### 현재 기능"
        )

        st.write(
            "✅ Shopify 상품 정보 추출"
        )

        st.write(
            "✅ GPT Vision 이미지 분석"
        )

        st.write(
            "✅ Start / End Pair 추천"
        )

        st.write(
            "✅ Product Lock"
        )

        st.write(
            "✅ 무음 Kling Prompt"
        )

        st.write(
            "✅ AI TikTok 콘텐츠 전략"
        )

        st.write(
            "✅ 5초 × 3 Scene Storyboard"
        )

        st.write(
            "✅ Scene별 Kling 영상 업로드"
        )

        st.write(
            "✅ FFmpeg 15초 영상 결합"
        )

        st.write(
            "✅ CapCut용 SRT / ZIP"
        )

        st.write(
            "❌ Kling API 자동 생성"
        )

        st.write(
            "❌ TikTok 자동 게시"
        )

        st.divider()

        if check_ffmpeg():
            st.success(
                "FFmpeg 사용 가능"
            )
        else:
            st.warning(
                "FFmpeg 미설치 또는 PATH 미등록"
            )

    product_url = st.text_input(
        "Shopify 상품 URL",
        placeholder=(
            "https://sockslover.net/products/xxxxx"
        ),
    )

    fetch_clicked = st.button(
        "상품 정보 가져오기",
        type="primary",
    )

    if fetch_clicked:
        if not product_url.strip():
            st.error(
                "상품 URL을 입력해주세요."
            )

        else:
            with st.spinner(
                "상품 페이지를 읽고 이미지를 추출하는 중입니다..."
            ):
                try:
                    html = fetch_html(
                        product_url.strip()
                    )

                    product = extract_shopify_product_data(
                        product_url.strip(),
                        html,
                    )

                    st.session_state[
                        "product"
                    ] = product

                    st.session_state[
                        "analysis_start_number"
                    ] = default_analysis_start_index(
                        product
                    )

                    for key in [
                        "vision_analysis",
                        "generated_prompt",
                        "selected_pair",
                        "selected_images",
                        "prompt_meta",
                        "scene_card",
                        "regen_prompt",
                        "content_strategy",
                        "storyboard",
                        "scene_image_map",
                        "final_video",
                    ]:
                        st.session_state.pop(
                            key,
                            None,
                        )

                    st.success(
                        "상품 정보 가져오기 완료"
                    )

                    st.rerun()

                except Exception as exc:
                    st.error(
                        f"상품 정보를 가져오지 못했습니다: {exc}"
                    )

    product = st.session_state.get(
        "product"
    )

    if not product:
        st.info(
            (
                "상품 URL을 입력하고 "
                "[상품 정보 가져오기]를 눌러주세요."
            )
        )
        return

    st.divider()

    render_product_summary(
        product
    )

    inferred = infer_category(
        product.get(
            "title",
            "",
        ),
        product.get(
            "description",
            "",
        ),
    )

    category = st.selectbox(
        "상품 카테고리",
        CATEGORY_OPTIONS,
        index=(
            CATEGORY_OPTIONS.index(
                inferred
            )
            if inferred
            in CATEGORY_OPTIONS
            else 0
        ),
    )

    if workflow.startswith(
        "Mini ShotFlow"
    ):
        render_mini_shotflow(
            product=product,
            category=category,
            model=model,
            api_key=api_key,
            max_vision_images=max_vision_images,
        )

    else:
        render_storyflow(
            product=product,
            category=category,
            model=model,
            api_key=api_key,
            max_vision_images=max_vision_images,
        )


if __name__ == "__main__":
    main()
