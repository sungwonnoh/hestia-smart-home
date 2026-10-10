/// 약을 먹는 식사 기준.
enum MealTiming {
  beforeMeal('BEFORE_MEAL', '식전'),
  rightAfterMeal('RIGHT_AFTER_MEAL', '식사 직후'),
  afterMeal30('AFTER_MEAL_30MIN', '식후 30분');

  const MealTiming(this.wireName, this.label);
  final String wireName;
  final String label;

  static MealTiming? fromWire(String? value) {
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return null;
  }
}

/// 하루 중 약 먹는 때.
enum DoseSlot {
  breakfast('BREAKFAST', '아침', isMeal: true),
  lunch('LUNCH', '점심', isMeal: true),
  dinner('DINNER', '저녁', isMeal: true),
  bedtime('BEDTIME', '자기 전', isMeal: false);

  const DoseSlot(this.wireName, this.label, {required this.isMeal});
  final String wireName;
  final String label;

  /// 식사와 연결되는 때. 자기 전은 식사 기준이 없다.
  final bool isMeal;

  static DoseSlot? fromWire(String? value) {
    for (final s in values) {
      if (s.wireName == value) return s;
    }
    return null;
  }
}

/// 사용자가 등록한 복약 일정.
///
/// 앱은 일정만 저장한다. 알림 시점(식사 감지, 평소 식사 시간 대체)은
/// Context Engine이 판단한다.
class Medication {
  const Medication({
    this.id = '',
    required this.name,
    required this.slots,
    this.mealTiming,
    required this.days,
    this.startDate,
    this.endDate,
    this.refillRequired = false,
  });

  /// 저장 전에는 빈 문자열. 서버가 정한다.
  final String id;

  /// 약 이름 (예: 혈압약).
  final String name;

  /// 하루 순서(아침 → 자기 전)로 정렬된 복용 시점.
  final List<DoseSlot> slots;

  /// 아침·점심·저녁이 있을 때만 쓴다.
  final MealTiming? mealTiming;

  /// 며칠분.
  final int days;

  /// 복용 시작일. 저장 전 null이면 서버가 등록한 날로 정한다.
  final DateTime? startDate;

  /// 마지막 복용일. 서버가 계산한다.
  final DateTime? endDate;

  /// 주기적으로 처방받아야 하는 약.
  final bool refillRequired;

  static const refillWarningDays = 3;

  bool get needsMealTiming => slots.any((s) => s.isMeal);

  /// "아침·저녁 · 식후 30분", 자기 전만 있으면 "자기 전".
  String get scheduleLabel {
    final when = [
      for (final s in DoseSlot.values)
        if (slots.contains(s)) s.label,
    ].join('·');
    final timing = needsMealTiming ? mealTiming?.label : null;
    return timing == null ? when : '$when · $timing';
  }

  /// 오늘을 포함해 남은 복용 일수. 기간이 끝났으면 0.
  int? remainingDays(DateTime now) {
    final end = endDate;
    if (end == null) return null;
    final today = DateTime(now.year, now.month, now.day);
    final left = DateTime(end.year, end.month, end.day).difference(today).inDays + 1;
    return left.clamp(0, days);
  }

  /// 처방을 다시 받아야 할 때가 가까움.
  bool needsRefill(DateTime now) {
    final left = remainingDays(now);
    return refillRequired && left != null && left <= refillWarningDays;
  }

  Medication copyWith({
    String? name,
    List<DoseSlot>? slots,
    MealTiming? mealTiming,
    bool clearMealTiming = false,
    int? days,
    bool? refillRequired,
  }) =>
      Medication(
        id: id,
        name: name ?? this.name,
        slots: slots ?? this.slots,
        mealTiming: clearMealTiming ? null : mealTiming ?? this.mealTiming,
        days: days ?? this.days,
        startDate: startDate,
        endDate: endDate,
        refillRequired: refillRequired ?? this.refillRequired,
      );

  factory Medication.fromJson(Map<String, dynamic> json) => Medication(
        id: json['id'] as String,
        name: json['name'] as String,
        slots: (json['slots'] as List<dynamic>? ?? const [])
            .map((e) => DoseSlot.fromWire(e as String?))
            .whereType<DoseSlot>()
            .toList(),
        mealTiming: MealTiming.fromWire(json['mealTiming'] as String?),
        days: json['days'] as int,
        startDate: _parseDate(json['startDate']),
        endDate: _parseDate(json['endDate']),
        refillRequired: json['refillRequired'] as bool? ?? false,
      );

  /// 저장 요청 본문. id·endDate는 서버가 정하므로 보내지 않는다.
  Map<String, dynamic> toJson() => {
        'name': name.trim(),
        'slots': [
          for (final s in DoseSlot.values)
            if (slots.contains(s)) s.wireName,
        ],
        'mealTiming': needsMealTiming ? mealTiming?.wireName : null,
        'days': days,
        if (startDate != null) 'startDate': formatDate(startDate!),
        'refillRequired': refillRequired,
      };

  static String formatDate(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

  /// "2026-10-09" → 로컬 날짜.
  static DateTime? _parseDate(Object? value) {
    if (value is! String) return null;
    final parsed = DateTime.tryParse(value);
    return parsed == null ? null : DateTime(parsed.year, parsed.month, parsed.day);
  }
}
