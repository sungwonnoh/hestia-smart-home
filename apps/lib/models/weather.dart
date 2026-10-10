/// 기상청 강수형태 (초단기실황 PTY).
enum PrecipType {
  none('NONE', '강수 없음'),
  rain('RAIN', '비'),
  rainSnow('RAIN_SNOW', '비 또는 눈'),
  snow('SNOW', '눈'),
  drizzle('DRIZZLE', '빗방울'),
  drizzleSnow('DRIZZLE_SNOW', '빗방울·눈날림'),
  snowFlurry('SNOW_FLURRY', '눈날림'),
  unknown('', '');

  const PrecipType(this.wireName, this.label);
  final String wireName;
  final String label;

  bool get isSnow => this == snow || this == snowFlurry || this == drizzleSnow;

  static PrecipType fromWire(String? value) {
    if (value == null) return unknown;
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return unknown;
  }
}

/// 집 구역에 발효 중인 기상특보 하나.
class WeatherWarning {
  const WeatherWarning({
    required this.name,
    required this.type,
    required this.level,
  });

  /// 기상청 원문 (예: 폭염경보).
  final String name;

  /// HEAT / COLD_WAVE / HEAVY_RAIN / ...
  final String type;

  /// ADVISORY(주의보) / WARNING(경보) / SEVERE_WARNING(중대경보)
  final String level;

  /// 주의보보다 높은 단계.
  bool get isSevere => level == 'WARNING' || level == 'SEVERE_WARNING';

  factory WeatherWarning.fromJson(Map<String, dynamic> json) => WeatherWarning(
        name: (json['name'] ?? '') as String,
        type: (json['type'] ?? 'OTHER') as String,
        level: (json['level'] ?? 'OTHER') as String,
      );

  Map<String, dynamic> toJson() =>
      {'name': name, 'type': type, 'level': level};
}

/// RPi4가 기상청에서 받아 온 바깥 날씨.
///
/// 앱은 보여주기만 한다. 폭염 판단 등은 Context Engine이 한다.
class Weather {
  const Weather({
    required this.locationName,
    this.observedAt,
    this.temperatureC,
    this.humidityPct,
    this.precipitationMm,
    this.precipType = PrecipType.unknown,
    this.windSpeedMs,
    this.warnings,
    this.stale = false,
  });

  final String locationName;

  /// 기상청 관측 정시. 관측은 1시간에 한 번이다.
  final DateTime? observedAt;
  final double? temperatureC;
  final double? humidityPct;

  /// 1시간 강수량.
  final double? precipitationMm;
  final PrecipType precipType;
  final double? windSpeedMs;

  /// 발효 중인 특보. null이면 아직 모름, 빈 목록이면 특보 없음.
  final List<WeatherWarning>? warnings;

  /// 관측이 오래됨 (RPi4 갱신이 끊겼을 수 있다).
  final bool stale;

  bool get raining =>
      precipType != PrecipType.none && precipType != PrecipType.unknown;

  factory Weather.fromJson(Map<String, dynamic> json) {
    final rawWarnings = json['warnings'];
    return Weather(
      locationName: (json['locationName'] ?? '') as String,
      observedAt: json['observedAt'] is String
          ? DateTime.tryParse(json['observedAt'] as String)?.toLocal()
          : null,
      temperatureC: (json['temperatureC'] as num?)?.toDouble(),
      humidityPct: (json['humidityPct'] as num?)?.toDouble(),
      precipitationMm: (json['precipitationMm'] as num?)?.toDouble(),
      precipType: PrecipType.fromWire(json['precipType'] as String?),
      windSpeedMs: (json['windSpeedMs'] as num?)?.toDouble(),
      warnings: rawWarnings is List
          ? rawWarnings
              .whereType<Map<String, dynamic>>()
              .map(WeatherWarning.fromJson)
              .toList()
          : null,
      stale: json['stale'] as bool? ?? false,
    );
  }
}
