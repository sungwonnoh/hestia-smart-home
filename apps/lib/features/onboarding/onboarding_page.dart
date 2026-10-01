import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../app/theme.dart';
import '../../core/utils/display_labels.dart';
import 'onboarding_controller.dart';
import 'widgets/setup_editors.dart';

/// 최초 설정: Welcome → 방 → 가전 → 배치 → 알림 → 완료.
class OnboardingPage extends StatefulWidget {
  const OnboardingPage({super.key});

  @override
  State<OnboardingPage> createState() => _OnboardingPageState();
}

class _OnboardingPageState extends State<OnboardingPage> {
  late final OnboardingController _controller =
      OnboardingController(HestiaScope.of(context).repository);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _complete() async {
    final ok = await _controller.complete();
    if (!ok && mounted && _controller.error != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('설정을 저장하지 못했습니다. ${_controller.error}')),
      );
    }
  }

  void _finish() {
    Navigator.of(context).pushReplacementNamed(AppRoutes.shell);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: ListenableBuilder(
          listenable: _controller,
          builder: (context, _) {
            final step = _controller.step;
            return switch (step) {
              OnboardingStep.welcome => _WelcomeView(onStart: _controller.next),
              OnboardingStep.done => _DoneView(
                  roomCount: _controller.rooms.length,
                  deviceCount: _controller.devices.length,
                  onFinish: _finish,
                ),
              _ => _StepScaffold(
                  controller: _controller,
                  onComplete: _complete,
                ),
            };
          },
        ),
      ),
    );
  }
}

class _WelcomeView extends StatelessWidget {
  const _WelcomeView({required this.onStart});

  final VoidCallback onStart;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.local_fire_department_rounded,
                size: 72,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(height: 8),
              Text(
                'HESTIA',
                style: theme.textTheme.displayMedium?.copyWith(
                  fontWeight: FontWeight.w800,
                  letterSpacing: 6,
                  color: theme.colorScheme.primary,
                ),
              ),
              const SizedBox(height: 16),
              Text(
                '생활 패턴을 이해하는\n나만의 스마트홈',
                textAlign: TextAlign.center,
                style: theme.textTheme.headlineSmall?.copyWith(height: 1.4),
              ),
              const SizedBox(height: 40),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: onStart,
                  child: const Text('시작하기'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StepScaffold extends StatelessWidget {
  const _StepScaffold({required this.controller, required this.onComplete});

  final OnboardingController controller;
  final VoidCallback onComplete;

  static const _titles = {
    OnboardingStep.rooms: ('어떤 공간을 사용하고 있나요?', '여러 개를 선택할 수 있어요.'),
    OnboardingStep.devices: ('사용 중인 가전을 선택해주세요', 'HESTIA가 상태를 확인할 가전입니다.'),
    OnboardingStep.placement: ('가전이 어디에 있나요?', '알림을 가까운 곳으로 전달할 때 사용해요.'),
    OnboardingStep.preferences: ('알림을 어떻게 받을까요?', '언제든 설정에서 바꿀 수 있어요.'),
  };

  @override
  Widget build(BuildContext context) {
    final step = controller.step;
    final (title, subtitle) = _titles[step]!;
    final index = step.index; // rooms=1 ... preferences=4
    const total = 4;
    final isLast = step == OnboardingStep.preferences;
    final theme = Theme.of(context);

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(
                    '$index / $total',
                    style: theme.textTheme.labelLarge?.copyWith(
                      color: theme.colorScheme.primary,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        value: index / total,
                        minHeight: 8,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text(title, style: theme.textTheme.headlineSmall),
              const SizedBox(height: 4),
              Text(subtitle, style: theme.textTheme.bodyLarge),
            ],
          ),
        ),
        Expanded(
          child: Scrollbar(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
              child: _StepBody(controller: controller),
            ),
          ),
        ),
        _BottomBar(
          onBack: controller.back,
          onNext: controller.canProceed && !controller.saving
              ? (isLast ? onComplete : controller.next)
              : null,
          nextLabel: isLast ? '설정 완료' : '다음',
          busy: controller.saving,
        ),
      ],
    );
  }
}

class _StepBody extends StatelessWidget {
  const _StepBody({required this.controller});

  final OnboardingController controller;

  @override
  Widget build(BuildContext context) {
    switch (controller.step) {
      case OnboardingStep.rooms:
        return RoomSelector(
          selectedIds: controller.selectedRoomIds,
          onToggle: controller.toggleRoom,
        );
      case OnboardingStep.devices:
        return DeviceSelector(
          selected: controller.selectedDevices,
          onToggle: controller.toggleDevice,
        );
      case OnboardingStep.placement:
        final devices = controller.devices;
        if (devices.isEmpty) {
          return const Padding(
            padding: EdgeInsets.symmetric(vertical: 32),
            child: Text(
              '선택한 가전이 없습니다. 다음으로 넘어가세요.',
              style: TextStyle(fontSize: 18),
            ),
          );
        }
        return Column(
          children: [
            for (final type in devices)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: DevicePlacementRow(
                  icon: DeviceLabels.icon(type),
                  title: type.label,
                  rooms: controller.rooms,
                  selectedRoomId: controller.placements[type],
                  onSelected: (roomId) => controller.place(type, roomId),
                ),
              ),
          ],
        );
      case OnboardingStep.preferences:
        return PreferencesForm(
          value: controller.preferences,
          onChanged: controller.updatePreferences,
        );
      case OnboardingStep.welcome:
      case OnboardingStep.done:
        return const SizedBox.shrink();
    }
  }
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({
    required this.onBack,
    required this.onNext,
    required this.nextLabel,
    required this.busy,
  });

  final VoidCallback onBack;
  final VoidCallback? onNext;
  final String nextLabel;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: scheme.surface,
        border: Border(top: BorderSide(color: scheme.outlineVariant)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: busy ? null : onBack,
                child: const Text('이전'),
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: onNext,
                child: busy
                    ? const SizedBox.square(
                        dimension: 28,
                        child: CircularProgressIndicator(strokeWidth: 3),
                      )
                    : Text(nextLabel),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DoneView extends StatelessWidget {
  const _DoneView({
    required this.roomCount,
    required this.deviceCount,
    required this.onFinish,
  });

  final int roomCount;
  final int deviceCount;
  final VoidCallback onFinish;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.check_circle_rounded,
                size: 80,
                color: HestiaColors.ok,
              ),
              const SizedBox(height: 16),
              Text('설정이 완료되었습니다', style: theme.textTheme.headlineSmall),
              const SizedBox(height: 8),
              Text(
                '공간 $roomCount곳 · 가전 $deviceCount개',
                style: theme.textTheme.titleMedium,
              ),
              const SizedBox(height: 16),
              Text(
                '현재 상태는 HESTIA가 센서로 판단해\n홈 화면에서 알려드립니다.',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyLarge,
              ),
              const SizedBox(height: 32),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: onFinish,
                  child: const Text('HESTIA 시작하기'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
