/// Context Engine이 판단한 상황 하나 (activity, away 등).
///
/// 앱은 이 값을 계산하지 않는다. 받은 그대로 표시한다.
/// context마다 구조가 다르다. presence는 state 대신 area/areas를 보낸다.
class ContextState {
  const ContextState({
    required this.name,
    this.state = unknown,
    this.since,
    this.confidence,
    this.factors = const {},
    this.attributes = const {},
  });

  static const unknown = 'UNKNOWN';

  final String name;

  /// 대표 상태. 보내지 않는 context(presence 등)는 [unknown].
  final String state;
  final DateTime? since;
  final double? confidence;

  /// Context Engine이 남긴 근거 원본. 구조가 바뀌어도 받을 수 있게 Map으로 둔다.
  final Map<String, dynamic> factors;

  /// 위 필드 외의 값 (presence의 area/areas, wake의 진행 플래그 등).
  final Map<String, dynamic> attributes;

  /// presence가 보낸 현재 공간 id (예: kitchen).
  String? get area {
    final value = attributes['area'];
    return value is String && value.isNotEmpty ? value : null;
  }

  static const _knownKeys = {'state', 'since', 'confidence', 'factors'};

  factory ContextState.fromJson(String name, Map<String, dynamic> json) =>
      ContextState(
        name: name,
        state: json['state']?.toString() ?? unknown,
        since: _parseTime(json['since']),
        confidence: (json['confidence'] as num?)?.toDouble(),
        factors: Map<String, dynamic>.from(
          json['factors'] as Map? ?? const {},
        ),
        attributes: {
          for (final e in json.entries)
            if (!_knownKeys.contains(e.key)) e.key: e.value,
        },
      );

  Map<String, dynamic> toJson() => {
        if (state != unknown) 'state': state,
        if (since != null) 'since': since!.millisecondsSinceEpoch ~/ 1000,
        if (confidence != null) 'confidence': confidence,
        if (factors.isNotEmpty) 'factors': factors,
        ...attributes,
      };

  /// API는 epoch seconds, 다른 곳은 ISO 문자열일 수 있어 둘 다 받는다.
  static DateTime? _parseTime(Object? value) {
    if (value is num) {
      return DateTime.fromMillisecondsSinceEpoch((value * 1000).round());
    }
    if (value is String) return DateTime.tryParse(value)?.toLocal();
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
