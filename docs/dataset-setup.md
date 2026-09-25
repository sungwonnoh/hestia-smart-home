# Dataset Setup

## Dataset

현재 KDE Baseline 검증에는 **CASAS Aruba Smart Home Dataset**을 사용합니다.

Aruba는 1인 거주자의 장기간 스마트홈 센서 및 Activity Label 데이터로,
사용자 개인의 생활 시간 패턴을 검증하기 위한 초기 데이터셋입니다.

Dataset:

https://zenodo.org/records/17180309

다운로드 파일:

```text
new_labeled_data.zip
```

압축 파일에는 다음 데이터가 포함됩니다.

```text
aruba.txt
cairo.txt
milan.txt
tulum1.txt
tulum2.txt
```

현재 HESTIA에서는 `aruba.txt`를 사용합니다.

---

## 1. 데이터 폴더 생성

레포지토리 루트에서 실행합니다.

```bash
mkdir -p data/raw/casas/aruba
mkdir -p data/processed/aruba
```

---

## 2. 데이터 다운로드

Zenodo에서 `new_labeled_data.zip`을 다운로드한 뒤 다음 위치로 이동합니다.

```text
data/raw/casas/aruba/new_labeled_data.zip
```

macOS 예시:

```bash
mv ~/Downloads/new_labeled_data.zip data/raw/casas/aruba/
```

---

## 3. 압축 해제

```bash
cd data/raw/casas/aruba
unzip new_labeled_data.zip
```

레포 루트로 돌아갑니다.

```bash
cd ../../../..
```

---

## 4. Meal Preparation 추출

Aruba 원본 데이터에서 다음 Activity의 시작 이벤트를 추출합니다.

```text
Meal_Preparation begin
```

실행:

```bash
python3 scripts/datasets/parse_aruba.py
```

출력:

```text
data/processed/aruba/meal_preparation.csv
```

현재 데이터 기준 약 1,606개의 `Meal_Preparation begin` 이벤트가 추출됩니다.

---

## 5. Breakfast Proxy 생성

CASAS에는 `Breakfast_Preparation`이라는 별도 라벨이 없기 때문에
HESTIA MVP에서는 다음 규칙으로 아침 준비 시각을 정의합니다.

```text
05:00 ~ 11:00 사이에 발생한
하루의 첫 번째 Meal_Preparation
```

실행:

```bash
python3 scripts/datasets/filter_breakfast.py
```

출력:

```text
data/processed/aruba/breakfast_preparation.csv
```

현재 Aruba 기준 약 212일의 아침 대표 이벤트가 생성됩니다.

---

## Data Version Control

원본 및 전처리 데이터는 GitHub에 업로드하지 않습니다.

`.gitignore` 예시:

```gitignore
data/raw/*
!data/raw/.gitkeep

data/processed/*
!data/processed/.gitkeep
```

빈 폴더 구조만 유지하려면:

```bash
touch data/raw/.gitkeep
touch data/processed/.gitkeep
```

GitHub에 포함하지 않는 파일 예시:

```text
new_labeled_data.zip
aruba.txt
meal_preparation.csv
breakfast_preparation.csv
```

대신 이 문서의 절차를 통해 동일한 데이터를 다시 생성합니다.

---

## Current Limitation

현재 `breakfast_preparation.csv`는 실제 `Breakfast` 라벨이 아니라
다음 규칙으로 생성한 MVP용 Proxy 데이터입니다.

```text
05:00 ~ 11:00
+
하루 첫 Meal_Preparation begin
```

따라서 현재 KDE는 전체 `meal_time` 패턴을 완전히 반영하는 모델이 아니라,
아침 Meal Preparation 패턴을 이용한 초기 검증 모델입니다.

향후에는 breakfast / lunch / dinner를 포함한 전체 Meal 이벤트를 이용해
`meal_time`을 multi-modal distribution으로 확장할 예정입니다.
