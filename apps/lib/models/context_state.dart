/// Context Engine이 판단한 상황 하나 (activity, away 등).
///
/// 앱은 이 값을 계산하지 않는다. 받은 그대로 표시한다.
class ContextState {
  const ContextState({
    required this.name,
    required this.state,
    this.since,
    this.confidence,
    this.factors = const {},
  });

  final String name;
  final String state;
  final DateTime? since;
  final double? confidence;

  /// Context Engine이 남긴 근거 원본. 구조가 바뀌어도 받을 수 있게 Map으로 둔다.
  final Map<String, dynamic> factors;

  factory ContextState.fromJson(String name, Map<String, dynamic> json) =>
      ContextState(
        name: name,
        state: (json['state'] ?? 'UNKNOWN').toString(),
        since: _parseTime(json['since']),
        confidence: (json['confidence'] as num?)?.toDouble(),
        factors: Map<String, dynamic>.from(
          json['factors'] as Map? ?? const {},
        ),
      );

  Map<String, dynamic> toJson() => {
        'state': state,
        if (since != null) 'since': since!.millisecondsSinceEpoch ~/ 1000,
        if (confidence != null) 'confidence': confidence,
        if (factors.isNotEmpty) 'factors': factors,
      };

  /// API 초안은 epoch seconds, 다른 곳은 ISO 문자열일 수 있어 둘 다 받는다.
  static DateTime? _parseTime(Object? value) {
    if (value is int) {
      return DateTime.fromMillisecondsSinceEpoch(value * 1000);
    }
    if (value is String) return DateTime.tryParse(value);
    return null;
  }
}

/// Context 이름. MQTT `hestia/context/{name}`과 같다.
abstract final class ContextName {
  static const activity = 'activity';
  static const presence = 'presence';
  static const wake = 'wake';
  static const away = 'away';
  static const occupancy = 'occupancy';
  static const suppression = 'suppression';

  /// 홈 화면 표시 순서.
  static const displayOrder = [
    away,
    activity,
    presence,
    wake,
    occupancy,
    suppression,
  ];
}

/// 현재 시점의 Context 묶음 (`GET /api/v1/context/current`).
class HestiaContext {
  const HestiaContext({
    this.states = const {},
    this.updatedAt,
  });

  final Map<String, ContextState> states;
  final DateTime? updatedAt;

  ContextState? operator [](String name) => states[name];

  bool get isEmpty => states.isEmpty;

  /// 표시 순서대로 정렬. 모르는 context는 뒤에 붙인다.
  List<ContextState> get ordered {
    final known = ContextName.displayOrder
        .where(states.containsKey)
        .map((n) => states[n]!);
    final rest = states.entries
        .where((e) => !ContextName.displayOrder.contains(e.key))
        .map((e) => e.value);
    return [...known, ...rest];
  }

  factory HestiaContext.fromJson(Map<String, dynamic> json) => HestiaContext(
        states: {
          for (final entry in json.entries)
            if (entry.value is Map<String, dynamic>)
              entry.key: ContextState.fromJson(
                entry.key,
                entry.value as Map<String, dynamic>,
              ),
        },
      );

  Map<String, dynamic> toJson() =>
      {for (final s in states.values) s.name: s.toJson()};
}
