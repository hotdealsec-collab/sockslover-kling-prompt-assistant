# SocksLover Kling Prompt Assistant

Shopify 상품 URL에서 상품 이미지와 기본 정보를 추출하고, Kling AI에 복사해서 사용할 수 있는 상품 영상 생성 프롬프트를 만들어주는 Streamlit MVP입니다.

## MVP v4.2 범위

지원 기능:

- Shopify 상품 URL 입력
- 상품명 / 설명 / 이미지 자동 추출
- 이미지 썸네일 표시 및 선택
- 상품 카테고리 / 영상 스타일 / 비율 / 길이 선택
- GPT를 이용한 Kling용 프롬프트 생성
- 영상 내 텍스트 금지 조건 자동 반영
- 선택 이미지 + 프롬프트 ZIP 다운로드
- 작업 로그 CSV 저장

범위 제외:

- Kling API 연동
- Runway/Pika API 연동
- Shopify 상품 미디어 자동 업로드
- 대표 이미지/대표 미디어 자동 교체
- 동영상 자동 생성

## 설치 방법

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 환경변수 설정

`.env.example`을 복사해서 `.env`를 만듭니다.

```bash
cp .env.example .env
```

`.env`에 OpenAI API Key를 입력합니다.

```env
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-4.1-mini
```

이미 기존 상품 설명 생성용 OpenAI API Key를 사용 중이라면 같은 Key를 사용할 수 있습니다.

## 실행 방법

```bash
streamlit run app.py
```

## 사용 흐름

1. Shopify 상품 URL을 입력합니다.
2. `상품 정보 가져오기` 버튼을 누릅니다.
3. 추출된 이미지 중 Kling에 사용할 이미지를 선택합니다.
4. 상품 카테고리, 영상 스타일, 비율, 길이를 선택합니다.
5. `Kling용 프롬프트 생성` 버튼을 누릅니다.
6. 생성된 프롬프트를 Kling에 복사합니다.
7. 선택 이미지 ZIP을 다운로드해 Kling에 업로드합니다.
8. Kling에서 영상 생성 후 사람이 검수합니다.
9. 최종 MP4를 Shopify 상품 미디어에 수동 등록합니다.

## 권장 운영 기준

- 초기 테스트 상품 수: 10개
- 영상 길이: 5초 우선
- 비율: Shopify 대표 미디어용 1:1 우선
- 영상 내 텍스트: 사용하지 않음
- 상품 왜곡 발생 시: 재생성 또는 사용 제외

## 프롬프트 원칙

앱은 GPT에게 다음 조건을 항상 반영하도록 요청합니다.

- 상품 디자인 유지
- 색상 유지
- 패턴 유지
- 소재감 유지
- 과도한 움직임 금지
- 텍스트 / 자막 / 로고 / 워터마크 금지
- 일본 온라인 쇼핑몰에 맞는 깔끔한 상품 영상 톤

## 폴더 구조

```text
sockslover-kling-prompt-assistant/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── outputs/
│   ├── images/
│   └── zips/
└── logs/
```

## 주의사항

Shopify 테마나 앱 구조에 따라 상품 페이지 HTML에서 추출되는 이미지가 달라질 수 있습니다. 처음에는 추출 결과를 사람이 확인하고, 불필요한 배너/아이콘 이미지는 체크 해제해서 사용하세요.

AI 영상은 실제 상품과 다르게 변형될 수 있습니다. Shopify 상품 대표 미디어로 등록하기 전 반드시 사람이 검수해야 합니다.
