import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../core/network/hestia_exception.dart';
import '../../core/widgets/state_views.dart';

/// 앱 시작. 설정이 있으면 Home, 없으면 Onboarding으로 보낸다.
class SplashPage extends StatefulWidget {
  const SplashPage({
    super.key,
    this.minDuration = const Duration(milliseconds: 800),
  });

  /// 로고를 보여주는 최소 시간.
  final Duration minDuration;

  @override
  State<SplashPage> createState() => _SplashPageState();
}

class _SplashPageState extends State<SplashPage> {
  String? _error;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
  if (_error != null) setState(() => _error = null);

  final repository = HestiaScope.of(context).repository;
  final navigator = Navigator.of(context);

  try {
    final setupFuture = repository.getSetup();
    final delayFuture = Future<void>.delayed(widget.minDuration);

    final setup = await setupFuture;
    await delayFuture;

    if (!mounted) return;

    navigator.pushReplacementNamed(
      setup == null ? AppRoutes.onboarding : AppRoutes.shell,
    );
  } on HestiaException catch (e) {
    if (mounted) setState(() => _error = e.message);
  }
}

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (_error != null) {
      return Scaffold(
        body: MessageView(
          icon: Icons.cloud_off_rounded,
          title: '설정 정보를 불러오지 못했습니다',
          message: _error,
          onAction: _boot,
        ),
      );
    }
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.local_fire_department_rounded,
              size: 88,
              color: theme.colorScheme.primary,
            ),
            const SizedBox(height: 12),
            Text(
              'HESTIA',
              style: theme.textTheme.displaySmall?.copyWith(
                fontWeight: FontWeight.w800,
                letterSpacing: 6,
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 32),
            const SizedBox.square(
              dimension: 32,
              child: CircularProgressIndicator(strokeWidth: 3),
            ),
          ],
        ),
      ),
    );
  }
}
