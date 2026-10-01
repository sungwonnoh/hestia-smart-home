/// 집 안의 공간.
///
/// HESTIA 판단에는 방 이름보다 [roles]가 중요하다.
/// 원룸처럼 하나의 공간이 여러 역할을 가질 수 있다.
class Room {
  const Room({
    required this.id,
    required this.name,
    this.roles = const [],
  });

  /// `config/homes/*.toml`의 area 값과 같은 체계 (예: living, kitchen).
  final String id;
  final String name;

  /// `config/homes/*.toml`의 roles 값 (예: LIVING, SLEEP, MEAL).
  final List<String> roles;

  factory Room.fromJson(Map<String, dynamic> json) => Room(
        id: json['id'] as String,
        name: json['name'] as String,
        roles: (json['roles'] as List<dynamic>? ?? const [])
            .map((e) => e.toString())
            .toList(),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'roles': roles,
      };

  @override
  bool operator ==(Object other) => other is Room && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

/// 공간 역할 값. Context Engine 설정과 문자열을 맞춘다.
abstract final class RoomRole {
  static const living = 'LIVING';
  static const sleep = 'SLEEP';
  static const meal = 'MEAL';
  static const hygiene = 'HYGIENE';
  static const laundry = 'LAUNDRY';
  static const entry = 'ENTRY';
  static const focus = 'FOCUS';
}

/// 사용자가 고르는 "쉬운 방 이름"과 내부 role 매핑.
///
/// UI는 템플릿을 보여주고, 저장 시 [toRoom]으로 [Room]을 만든다.
class RoomTemplate {
  const RoomTemplate({
    required this.id,
    required this.name,
    required this.roles,
    this.description,
  });

  final String id;
  final String name;
  final List<String> roles;
  final String? description;

  Room toRoom() => Room(id: id, name: name, roles: roles);

  static const catalog = <RoomTemplate>[
    RoomTemplate(id: 'living', name: '거실', roles: [RoomRole.living]),
    RoomTemplate(id: 'bedroom', name: '침실', roles: [RoomRole.sleep]),
    RoomTemplate(id: 'kitchen', name: '주방', roles: [RoomRole.meal]),
    RoomTemplate(id: 'bathroom', name: '욕실', roles: [RoomRole.hygiene]),
    RoomTemplate(id: 'utility', name: '다용도실', roles: [RoomRole.laundry]),
    RoomTemplate(id: 'entrance', name: '현관', roles: [RoomRole.entry]),
    RoomTemplate(
      id: 'studio',
      name: '원룸',
      roles: [RoomRole.living, RoomRole.sleep, RoomRole.meal],
      description: '생활·수면·식사를 한 공간에서',
    ),
    RoomTemplate(id: 'other', name: '기타', roles: []),
  ];

  static RoomTemplate? byId(String id) {
    for (final t in catalog) {
      if (t.id == id) return t;
    }
    return null;
  }
}
