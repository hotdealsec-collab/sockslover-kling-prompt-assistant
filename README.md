# SocksLover Mini ShotFlow

MVP v4.4 for SocksLover product video production.

This Streamlit app helps small Shopify stores create AI-video-ready assets without automating Kling or Shopify uploads.

## What it does

- Enter a Shopify product URL
- Extract product images from the product page HTML
- Display product image cards
- Analyze images with GPT Vision
- Recommend the best start/end image pair for Kling
- Generate a Scene Card
- Insert Product Lock rules automatically
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
3. Select the product category and video type
4. Run GPT Vision analysis
5. Review the recommended image pair
6. Generate the Scene Card and Kling prompt
7. Download the ZIP
8. Upload the two selected images to Kling manually
9. Paste the generated prompt into Kling
10. Review the output video
11. Record the take result in the Take Log
12. Upload only the accepted video to Shopify manually

## Video types

- Shopify Product Video: product page, 1:1, 5 seconds
- SNS Short Video: Instagram/X/Pinterest, 4:5, 10 seconds
- Ad Creative Test: ad creative testing, 1:1, 5 seconds
- Brand Mood Clip: category or brand mood, 16:9, 10 seconds

## Product Lock

The app automatically adds Product Lock rules to reduce AI deformation.

Examples:

- Do not change product color, shape, material, texture, or proportions.
- Do not add text, captions, typography, logos, watermarks, or fake letters.
- For bags: preserve strap, handle, buckle, zipper, stitching, and structure.
- For socks: preserve pattern, color, length, fabric texture, and ribbing.

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
