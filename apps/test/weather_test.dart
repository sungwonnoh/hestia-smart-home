import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/core/utils/display_labels.dart';
import 'package:hestia_flutter_test/models/weather.dart';

Map<String, dynamic> _json({Object? warnings = const [], bool stale = false}) =>
    {
      'locationName': '서울',
      'observedAt': '2026-10-10T14:00:00+09:00',
      'temperatureC': 31.2,
      'humidityPct': 58.0,
      'precipitationMm': 1.5,
      'precipType': 'RAIN',
      'windSpeedMs': 2.0,
      'warnings': warnings,
      'stale': stale,
    };

void main() {
  test('특보: null은 모름, 빈 목록은 없음', () {
    expect(Weather.fromJson(_json(warnings: null)).warnings, isNull);
    expect(Weather.fromJson(_json()).warnings, isEmpty);

    final w = Weather.fromJson(_json(warnings: [
      {'name': '폭염주의보', 'type': 'HEAT', 'level': 'ADVISORY'},
      {'name': '호우경보', 'type': 'HEAVY_RAIN', 'level': 'WARNING'},
    ]));
    expect(w.warnings!.map((x) => x.isSevere), [false, true]);
  });

  test('모르는 강수형태는 unknown, 강수 없음으로 보지 않는다', () {
    final w = Weather.fromJson({..._json(), 'precipType': 'HAIL'});
    expect(w.precipType, PrecipType.unknown);
    expect(w.raining, isFalse);
  });

  test('표시 문구', () {
    final w = Weather.fromJson(_json());
    expect(WeatherLabels.temperature(w), '31.2°C');
    expect(WeatherLabels.details(w), '습도 58% · 비 1.5mm · 바람 2m/s');
    expect(WeatherLabels.icon(w), isNotNull);

    final now = DateTime.parse('2026-10-10T14:30:00+09:00').toLocal();
    expect(WeatherLabels.observed(w, now), endsWith('관측'));
    final stale = Weather.fromJson(_json(stale: true));
    expect(WeatherLabels.observed(stale, now), endsWith('업데이트 지연'));
  });

  test('값이 비어 있어도 깨지지 않는다', () {
    const w = Weather(locationName: '');
    expect(WeatherLabels.temperature(w), '--°C');
    expect(WeatherLabels.details(w), '');
    expect(WeatherLabels.observed(w, DateTime.now()), '관측 시각 모름');
  });
}
