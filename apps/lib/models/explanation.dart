/// 판단 근거 하나.
class ExplanationFactor {
  const ExplanationFactor({
    required this.label,
    this.satisfied = true,
    this.detail,
  });

  /// 사람이 읽을 수 있는 문장 (예: "주방 재실 감지").
  final String label;
  final bool satisfied;
  final String? detail;

  /// 문자열 하나, 또는 {label, satisfied, detail} 객체를 받는다.
  factory ExplanationFactor.fromJson(Object? json) {
    if (json is Map<String, dynamic>) {
      return ExplanationFactor(
        label: (json['label'] ?? json['name'] ?? '').toString(),
        satisfied: json['satisfied'] as bool? ?? true,
        detail: json['detail']?.toString(),
      );
    }
    return ExplanationFactor(label: json.toString());
  }

  Map<String, dynamic> toJson() => {
        'label': label,
        'satisfied': satisfied,
        if (detail != null) 'detail': detail,
      };
}

/// Context Engine이 만든 판단 설명.
///
/// 앱은 새로운 판단을 하지 않는다. 엔진이 준 근거를 보여주기만 한다.
class Explanation {
  const Explanation({
    required this.id,
    required this.contextName,
    required this.state,
    this.confidence,
    this.factors = const [],
    this.action,
    this.createdAt,
  });

  final String id;
  final String contextName;
  final String state;
  final double? confidence;
  final List<ExplanationFactor> factors;

  /// 판단 결과로 취한 행동 (예: "복약 알림을 준비했습니다.").
  final String? action;
  final DateTime? createdAt;

  /// factors는 List 또는 Map 모두 허용한다.
  /// Map이면 `key: value`를 그대로 근거 문장으로 만든다.
  factory Explanation.fromJson(Map<String, dynamic> json) {
    final raw = json['factors'];
    final factors = switch (raw) {
      List<dynamic> list => list.map(ExplanationFactor.fromJson).toList(),
      Map<String, dynamic> map => map.entries
          .map((e) => ExplanationFactor(label: '${e.key}: ${e.value}'))
          .toList(),
      _ => <ExplanationFactor>[],
    };
    return Explanation(
      id: (json['id'] ?? '') as String,
      contextName: (json['contextName'] ?? json['context'] ?? '') as String,
      state: (json['state'] ?? 'UNKNOWN') as String,
      confidence: (json['confidence'] as num?)?.toDouble(),
      factors: factors,
      action: json['action'] as String?,
      createdAt: json['createdAt'] is String
          ? DateTime.tryParse(json['createdAt'] as String)?.toLocal()
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'contextName': contextName,
        'state': state,
        if (confidence != null) 'confidence': confidence,
        'factors': factors.map((f) => f.toJson()).toList(),
        if (action != null) 'action': action,
        if (createdAt != null) 'createdAt': createdAt!.toIso8601String(),
      };
}
