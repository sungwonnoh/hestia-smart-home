import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../app/theme.dart';
import '../../core/constants/app_config.dart';
import '../../core/network/hestia_exception.dart';
import '../../core/widgets/hestia_card.dart';
import '../../repositories/mock_hestia_repository.dart';

class SystemInfoPage extends StatefulWidget {
  const SystemInfoPage({super.key});

  @override
  State<SystemInfoPage> createState() => _SystemInfoPageState();
}

class _SystemInfoPageState extends State<SystemInfoPage> {
  bool? _connected;

  @override
  void initState() {
    super.initState();
    _checkConnection(showProgress: false);
  }

  Future<void> _checkConnection({bool showProgress = true}) async {
    // initState에서는 setState를 부를 수 없다. 초기값이 이미 null이다.
    if (showProgress) setState(() => _connected = null);
    try {
      await HestiaScope.of(context).repository.getSetup();
      if (mounted) setState(() => _connected = true);
    } on HestiaException {
      if (mounted) setState(() => _connected = false);
    }
  }

  Future<void> _resetSetup(DemoControls demo) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('초기 설정을 다시 할까요?'),
        content: const Text('등록한 공간·가전·알림 설정이 지워지고 처음 화면으로 돌아갑니다.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('취소'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('다시 설정'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final scope = HestiaScope.of(context);
    final navigator = Navigator.of(context);
    await demo.resetSetup();
    await scope.notifications.load();
    navigator.pushNamedAndRemoveUntil(AppRoutes.onboarding, (_) => false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final repository = HestiaScope.of(context).repository;
    final demo = switch (repository) {
      DemoControls d => d,
      _ => null,
    };

    return Scaffold(
      appBar: AppBar(title: const Text('시스템 정보')),
      body: Scrollbar(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
          children: [
            const SectionHeader('앱'),
            HestiaCard(
              padding: EdgeInsets.zero,
              child: Column(
                children: [
                  const ListTile(
                    title: Text('버전'),
                    trailing: Text(
                      AppConfig.appVersion,
                      style: TextStyle(fontSize: 18),
                    ),
                  ),
                  ListTile(
                    title: const Text('데이터 소스'),
                    subtitle: Text(
                      demo != null ? 'Mock 데이터 (서버 연결 전)' : 'HESTIA API',
                    ),
                  ),
                  const ListTile(
                    title: Text('API 주소'),
                    subtitle: Text('${AppConfig.apiBaseUrl}\n'
                        '빌드 시 --dart-define=API_BASE_URL로 지정'),
                    isThreeLine: true,
                  ),
                  ListTile(
                    title: const Text('연결 상태'),
                    trailing: switch (_connected) {
                      null => const SizedBox.square(
                          dimension: 24,
                          child: CircularProgressIndicator(strokeWidth: 3),
                        ),
                      true => const Text(
                          '정상',
                          style: TextStyle(
                            color: HestiaColors.ok,
                            fontSize: 18,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      false => const Text(
                          '연결 끊김',
                          style: TextStyle(
                            color: HestiaColors.warning,
                            fontSize: 18,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                    },
                    onTap: _checkConnection,
                  ),
                ],
              ),
            ),
            if (demo != null) ...[
              const SectionHeader('시연 도구'),
              Text(
                'Mock 데이터로 실행 중일 때만 보입니다.',
                style: theme.textTheme.bodyMedium,
              ),
              const SizedBox(height: 12),
              HestiaCard(
                padding: EdgeInsets.zero,
                child: Column(
                  children: [
                    SwitchListTile(
                      title: const Text('서버 연결 끊김 흉내'),
                      subtitle: const Text('오프라인 화면과 마지막 상태 표시를 확인합니다.'),
                      value: demo.offline,
                      onChanged: (v) {
                        setState(() => demo.setOffline(v));
                        _checkConnection();
                      },
                    ),
                    ListTile(
                      leading: const Icon(Icons.notification_add_rounded),
                      title: const Text('새 알림 보내기'),
                      subtitle: const Text('안전 → 복약 → 공기질 순서로 발생합니다.'),
                      onTap: () {
                        final n = demo.pushDemoNotification();
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('알림 발생: ${n.title}')),
                        );
                      },
                    ),
                    ListTile(
                      leading: const Icon(
                        Icons.restart_alt_rounded,
                        color: HestiaColors.safety,
                      ),
                      title: const Text('초기 설정 다시 하기'),
                      onTap: () => _resetSetup(demo),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
