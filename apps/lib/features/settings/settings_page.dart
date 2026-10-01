import 'package:flutter/material.dart';

import '../../app/routes.dart';
import '../../core/widgets/hestia_card.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  static const _items = [
    (Icons.meeting_room_rounded, '공간 관리', '사용하는 방과 공간', AppRoutes.settingsRooms),
    (Icons.devices_rounded, '가전 관리', '가전 추가·삭제와 위치', AppRoutes.settingsDevices),
    (Icons.notifications_active_rounded, '알림 설정', '방해 금지 시간, 민감도, 안전 알림',
        AppRoutes.settingsNotifications),
    (Icons.info_outline_rounded, '시스템 정보', '버전, 연결 상태, 시연 도구', AppRoutes.settingsSystem),
  ];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('설정')),
      body: Scrollbar(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
          children: [
            for (final (icon, title, subtitle, route) in _items)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: HestiaCard(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 18,
                  ),
                  onTap: () => Navigator.of(context).pushNamed(route),
                  child: Row(
                    children: [
                      Icon(icon, size: 32, color: theme.colorScheme.primary),
                      const SizedBox(width: 18),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(title, style: theme.textTheme.titleMedium),
                            Text(subtitle, style: theme.textTheme.bodyMedium),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right_rounded, size: 32),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
