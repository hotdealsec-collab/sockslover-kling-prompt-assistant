# SocksLover Kling Prompt Assistant

Shopify 상품 URL에서 상품 이미지를 자동 추출하고, GPT Vision으로 Kling AI용 시작/끝 이미지 pair를 추천한 뒤, Kling에 복사할 영상 생성 프롬프트를 만드는 Streamlit MVP입니다.

## MVP v4.3 기능

- Shopify 상품 URL 입력
- 상품명 / 설명 / 이미지 자동 추출
- 이미지 카드 표시
- GPT Vision으로 이미지 유형 / Kling 적합도 분석
- Kling 시작 프레임 / 끝 프레임용 이미지 pair TOP 3 추천
- 추천 사유와 주의사항 표시
- 선택된 pair 기반 Kling 프롬프트 자동 생성
- 영상 내 텍스트 / 자막 / 로고 금지 조건 자동 반영
- 선택 이미지 + 프롬프트 ZIP 다운로드
- 작업 로그 CSV 저장

## 제외 범위

- Kling API 자동 연동 없음
- Runway / Pika API 자동 연동 없음
- Shopify 상품 미디어 자동 업로드 없음
- 대표 미디어 자동 교체 없음

이 MVP는 사람이 Kling에서 직접 영상을 생성하고, Shopify에 수동으로 등록하는 반자동 운영 도구입니다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행

```bash
streamlit run app.py
```

## OpenAI API Key 사용 방식

OpenAI API Key는 `.env`에 저장하지 않습니다.
Streamlit 실행 후 왼쪽 사이드바의 `OpenAI API Key` 입력창에 사용할 때만 입력합니다.

API Key는 다음 위치에 저장되지 않습니다.

- GitHub repository
- `.env`
- ZIP 파일
- CSV 로그

## 기본 사용 흐름

1. Streamlit 실행
2. 왼쪽 사이드바에 OpenAI API Key 입력
3. Shopify 상품 URL 입력
4. `상품 정보 가져오기` 클릭
5. 이미지 카드 확인
6. `GPT Vision으로 분석하고 Pair 추천` 클릭
7. 추천 Pair TOP 3 중 하나 선택
8. 영상 스타일 / 비율 / 길이 선택
9. `선택 Pair 기반 Kling용 프롬프트 생성` 클릭
10. 생성된 프롬프트와 선택 이미지 2장을 Kling에 수동 입력
11. 영상 검수 후 Shopify에 수동 등록

## 추천 운영 원칙

Kling에서 시작/끝 프레임으로 사용할 이미지는 2장만 선택하는 것을 권장합니다.

좋은 조합:

- 단일 상품 전체컷 → 같은 상품의 약간 다른 각도
- 단일 상품 전체컷 → 같은 상품의 약간 가까운 컷
- 같은 배경/같은 스타일의 착용컷 2장

피해야 할 조합:

- 여러 상품이 함께 있는 이미지
- 콜라주 이미지
- 제품 단독컷 → 완전히 다른 모델 착샷
- 배경이 크게 다른 이미지 조합
- 상품 색상/형태가 다른 이미지 조합

## 폴더 구조

```text
sockslover-kling-prompt-assistant/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── outputs/
│   └── zips/
└── logs/
```

## GitHub 업로드 시 주의

`.env` 파일이나 실제 API Key는 절대 업로드하지 마세요.
`.env.example`만 업로드하면 됩니다.
