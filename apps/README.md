# HESTIA Flutter App

HESTIA 스마트홈의 RPi5 터치스크린용 Flutter 앱.
개발 기준은 저장소 루트의 `HESTIA_APP_DEVELOPMENT_SPEC_CODEX_FINAL.md`를 따른다.

## 현재 단계: Phase 1 — Flutter UI Only

서버/DB/MQTT 없이 Mock 데이터만으로 전체 시연 흐름이 동작한다.

```text
Mock Data → HestiaRepository → Controller(ChangeNotifier) → Flutter UI
```

- 판단(사용자 상태 추론)은 Context Engine이 한다. 앱은 받은 상태를 표시만 한다.
- Widget은 HTTP/MQTT에 직접 접근하지 않는다. 서버가 정해지면 Repository 구현체만 교체한다.
- 서버 주소는 `--dart-define=API_BASE_URL=...`로 주입한다 (Phase 2).

## 구조

```text
lib/
├── app/           앱 셸, 라우터, 테마, 의존성 Scope
├── core/          상수, 예외, 공통 위젯, 표시용 변환
├── models/        도메인 모델 (JSON 직렬화 포함)
├── repositories/  HestiaRepository 인터페이스 + Mock 구현
└── features/      onboarding / home / notifications / explanation / settings
```

## 실행

```bash
flutter pub get
flutter analyze
flutter test
flutter run -d macos   # MacBook에서 UI 확인용
```

MacBook 실행 성공은 ATLAS/RPi5 실행 성공을 의미하지 않는다.
RPi5 검증은 `hestia_flutter_test`(ATLAS 프로젝트)에 소스를 반영해 `flutter-atlas`로 수행한다.

## Git 제외 대상

`atlas/`, `build/`, `.dart_tool/`, `.idea/`, `.metadata`, `*.ipk`는 추적하지 않는다.
