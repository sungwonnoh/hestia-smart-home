/// 복약 시점 종류.
///
/// Context Engine(#40)이 받는 두 가지뿐이다. 식전·자기 전·'아침·저녁만' 약은
/// [fixed]로 시각을 정해 등록한다.
enum ScheduleType {
  /// 식후. 끼니를 고르지 않는다 — 엔진이 끼니마다(하루 최대 세 번) 알린다.
  afterMeal('AFTER_MEAL', '식후'),

  /// 정해진 시각.
  fixed('FIXED', '정해진 시각');

  const ScheduleType(this.wireName, this.label);
  final String wireName;
  final String label;

  static ScheduleType? fromWire(String? value) {
    for (final t in values) {
      if (t.wireName == value) return t;
    }
    return null;
  }
}

/// 언제 약을 먹는가.
class MedicationSchedule {
  const MedicationSchedule.afterMeal({this.delayMin = 30})
      : type = ScheduleType.afterMeal,
        times = const [];

  const MedicationSchedule.fixed(this.times)
      : type = ScheduleType.fixed,
        delayMin = null;

  final ScheduleType type;

  /// 식후만. 식사가 끝나고 몇 분 뒤 (0 = 식사 직후).
  final int? delayMin;

  /// 정해진 시각만. "HH:MM".
  final List<String> times;

  /// 앱에서 고를 수 있는 식후 지연 (분).
  static const afterMealDelays = [0, 30];

  static const maxTimes = 6;

  static String delayLabel(int minutes) =>
      minutes == 0 ? '식사 직후' : '식후 $minutes분';

  /// "식후 30분 · 끼니마다", "08:00 · 20:00"
  String get label => switch (type) {
        ScheduleType.afterMeal => '${delayLabel(delayMin ?? 30)} · 끼니마다',
        ScheduleType.fixed => sortedTimes.join(' · '),
      };

  /// 하루 순서, 중복 없이.
  List<String> get sortedTimes => ({...times}.toList()..sort());

  /// 저장할 수 있는 상태인가.
  bool get isComplete => switch (type) {
        ScheduleType.afterMeal => delayMin != null,
        ScheduleType.fixed =>
          times.isNotEmpty && sortedTimes.length <= maxTimes,
      };

  /// 서버가 보내지 않거나 모르는 값이면 null.
  static MedicationSchedule? fromJson(Object? json) {
    if (json is! Map<String, dynamic>) return null;
    return switch (ScheduleType.fromWire(json['type'] as String?)) {
      ScheduleType.afterMeal => MedicationSchedule.afterMeal(
          delayMin: (json['delayMin'] as num?)?.toInt() ?? 30),
      ScheduleType.fixed => MedicationSchedule.fixed([
          for (final t in json['times'] as List<dynamic>? ?? const [])
            if (t is String) t,
        ]),
      null => null,
    };
  }

  /// 쓰지 않는 값은 보내지 않는다 (식후의 times, 정해진 시각의 delayMin).
  Map<String, dynamic> toJson() => switch (type) {
        ScheduleType.afterMeal => {
            'type': type.wireName,
            'delayMin': delayMin ?? 30,
          },
        ScheduleType.fixed => {
            'type': type.wireName,
            'times': sortedTimes,
          },
      };

  @override
  bool operator ==(Object other) =>
      other is MedicationSchedule &&
      other.type == type &&
      other.delayMin == delayMin &&
      _sameList(other.sortedTimes, sortedTimes);

  @override
  int get hashCode => Object.hash(type, delayMin, Object.hashAll(sortedTimes));

  static bool _sameList(List<String> a, List<String> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  /// (8, 5) → "08:05"
  static String formatTime(int hour, int minute) =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';
}

/// 사용자가 등록한 복약 일정.
///
/// 앱은 일정만 저장한다. 알림 시점(식사 감지, 정해진 시각)은
/// Context Engine이 판단한다.
class Medication {
  const Medication({
    this.id = '',
    required this.name,
    required this.schedule,
    required this.days,
    this.startDate,
    this.endDate,
    this.refillRequired = false,
  });

  /// 저장 전에는 빈 문자열. 서버가 정한다.
  final String id;

  /// 약 이름 (예: 혈압약).
  final String name;

  /// 언제 먹는가.
  final MedicationSchedule schedule;

  /// 며칠분.
  final int days;

  /// 복용 시작일. 저장 전 null이면 서버가 등록한 날로 정한다.
  final DateTime? startDate;

  /// 마지막 복용일. 서버가 계산한다.
  final DateTime? endDate;

  /// 주기적으로 처방받아야 하는 약.
  final bool refillRequired;

  static const refillWarningDays = 3;

  /// "식후 30분 · 끼니마다", "08:00 · 20:00"
  String get scheduleLabel => schedule.label;

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
    MedicationSchedule? schedule,
    int? days,
    bool? refillRequired,
  }) =>
      Medication(
        id: id,
        name: name ?? this.name,
        schedule: schedule ?? this.schedule,
        days: days ?? this.days,
        startDate: startDate,
        endDate: endDate,
        refillRequired: refillRequired ?? this.refillRequired,
      );

  /// schedule을 읽을 수 없으면(모르는 type) 식후 30분으로 둔다.
  /// 화면에서 고쳐 저장할 수 있게 목록에서 빼지 않는다.
  factory Medication.fromJson(Map<String, dynamic> json) => Medication(
        id: json['id'] as String,
        name: json['name'] as String,
        schedule: MedicationSchedule.fromJson(json['schedule']) ??
            const MedicationSchedule.afterMeal(),
        days: json['days'] as int,
        startDate: _parseDate(json['startDate']),
        endDate: _parseDate(json['endDate']),
        refillRequired: json['refillRequired'] as bool? ?? false,
      );

  /// 저장 요청 본문. id·endDate는 서버가 정하므로 보내지 않는다.
  Map<String, dynamic> toJson() => {
        'name': name.trim(),
        'schedule': schedule.toJson(),
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
