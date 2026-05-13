# SocksLover Mini ShotFlow

MVP v4.7 Image Filter Guard for SocksLover product video production.

This Streamlit app helps small Shopify stores create AI-video-ready assets without automating Kling or Shopify uploads.

## What it does

- Enter a Shopify product URL
- Extract product images from the product page HTML
- Exclude or skip common/default images before GPT Vision analysis
- Display only the selected analysis target image cards
- Analyze images with GPT Vision
- Recommend the best start/end image pair for Kling using a Lifestyle First strategy
- Generate a Scene Card
- Insert Product Lock and Model Lock rules automatically
- Generate a Kling-ready prompt with no text overlay
- Save generation logs
- Save Kling take/result logs
- Generate regeneration instructions when Kling output fails
- Download selected images, prompt, pair analysis, and Scene Card as a ZIP

## What it does not do

- It does not call the Kling API
- It does not upload videos to Shopify automatically
- It does not store your OpenAI API key

## Install

```bash
pip install -r requirements.txt
streamlit run app.py
```

## OpenAI API Key

Enter your OpenAI API key in the Streamlit sidebar when using the app.

The key is used only during the current Streamlit session and is not saved to CSV, ZIP, or logs.

## Recommended workflow

1. Open the app
2. Enter a SocksLover Shopify product URL
3. Check the extracted image count
4. Adjust **분석 시작 이미지 번호** if the first images are default/common/recommended-product images
5. Select the product category, video type, and recommendation strategy
6. Run GPT Vision analysis
7. Review the recommended image pair, prioritizing model/lifestyle shots when appropriate
8. Generate the Scene Card and Kling prompt
9. Download the ZIP
10. Upload the two selected images to Kling manually
11. Paste the generated prompt into Kling
12. Review the output video
13. Record the take result in the Take Log
14. Upload only the accepted video to Shopify manually

## Image Filter Guard

Some SocksLover product pages can return many images from the whole page, including default images, recommended products, or unrelated category images before the actual product body images.

v4.7 adds:

- **분석 시작 이미지 번호** option
- analysis target range preview
- full extracted image preview in an expander
- GPT Vision fields for product relevance and default/common-image suspicion
- heuristic pair guard that ignores images marked as low relevance or common/default

Example:

```text
Total extracted images: 40
Actual product images start around: use 22
Set 분석 시작 이미지 번호 = 22
Vision analyzes use 22 ~ use 33 only
```

## Video types and strategy

- Shopify Product Video: product page, 1:1, 5 seconds
- SNS Short Video: Instagram/X/Pinterest, 4:5, 10 seconds
- Ad Creative Test: ad creative testing, 1:1, 5 seconds
- Brand Mood Clip: category or brand mood, 16:9, 10 seconds

Recommendation strategies:

- Lifestyle First / 착샷 우선: prioritizes model/lifestyle shots when the product is clearly visible.
- Balanced / 착샷 + 제품 균형: balances product recognition and usage context.
- Product Focus / 제품컷 중심: prioritizes product-only images when available.

## Product Lock / Model Lock

The app automatically adds Product Lock rules to reduce AI deformation.

Examples:

- Do not change product color, shape, material, texture, or proportions.
- Do not add text, captions, typography, logos, watermarks, or fake letters.
- For bags: preserve strap, handle, buckle, zipper, stitching, and structure.
- For socks: preserve pattern, color, length, fabric texture, and ribbing.
- For model shots: keep pose, outfit, body proportions, and product placement natural while keeping the product as the hero.

## Logs

The app creates two CSV logs:

```text
logs/generation_log.csv
logs/take_log.csv
```

These logs help manage prompt generation and Kling output quality over multiple products.

## GitHub upload notes

Upload the extracted folder contents to GitHub, not the ZIP file itself.

Recommended files/folders:

```text
app.py
requirements.txt
README.md
.env.example
.gitignore
outputs/
logs/
```

Do not upload a real `.env` file or API keys.

## v4.7 Image Filter Guard 업데이트

이번 버전은 실제 상품 이미지가 뒤쪽에 있는데 앞쪽 공통/디폴트 이미지가 Vision 분석 대상이 되는 문제를 줄이기 위한 업데이트입니다.

- 분석 시작 이미지 번호 수동 지정
- SocksLover에서 이미지가 22장 이상이면 기본 분석 시작 번호를 22로 제안
- 전체 추출 이미지와 분석 대상 이미지를 분리 표시
- GPT Vision 결과에 `relevance_score`, `is_global_default` 필드 추가
- heuristic pair 추천에서 공통/디폴트/저관련 이미지 제외
- 동일 이미지 Start/End pair 자동 제외 유지

목적은 “상품과 무관한 이미지가 추천 Pair에 들어가는 문제를 줄이고, 실제 상품 상세 이미지/모델 착샷 구간만 분석하도록 만드는 것”입니다.
