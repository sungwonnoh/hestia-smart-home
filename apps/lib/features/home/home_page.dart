import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/routes.dart';
import '../../app/theme.dart';
import '../../core/state/async_controller.dart';
import '../../core/utils/display_labels.dart';
import '../../core/utils/time_format.dart';
import '../../core/widgets/hestia_card.dart';
import '../../core/widgets/state_views.dart';
import '../../models/context_state.dart';
import '../../models/device.dart';
import '../../models/medication.dart';
import '../../models/notification_item.dart';
import 'home_controller.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.onOpenNotifications});

  /// 알림 탭으로 이동.
  final VoidCallback onOpenNotifications;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late final HomeController _controller;

  @override
  void initState() {
    super.initState();
    _controller = HomeController(HestiaScope.of(context).repository)..load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    await Future.wait([
      _controller.load(),
      HestiaScope.of(context).notifications.load(),
    ]);
  }

  /// 복약 관리(목록) 또는 바로 추가 화면을 연 뒤 홈을 다시 읽는다.
  Future<void> _openMedication(String route) async {
    await Navigator.of(context).pushNamed(route);
    if (mounted) await _controller.load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _Header(controller: _controller, onRefresh: _refresh),
            Expanded(
              child: AsyncStateView<HomeData>(
                controller: _controller,
                builder: (context, data) => RefreshIndicator(
                  onRefresh: _refresh,
                  child: Scrollbar(
                    child: ListView(
                      padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
                      children: [
                        _ContextSection(hestiaContext: data.context),
                        _MedicationSection(
                          medications: data.medications,
                          onOpen: _openMedication,
                        ),
                        _RoomSection(data: data, controller: _controller),
                        _DeviceSection(data: data, controller: _controller),
                        _RecentNotifications(
                          onSeeAll: widget.onOpenNotifications,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.controller, required this.onRefresh});

  final HomeController controller;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 16, 12, 8),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'HESTIA',
                  style: theme.textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w800,
                    letterSpacing: 3,
                    color: theme.colorScheme.primary,
                  ),
                ),
                Text('안녕하세요', style: theme.textTheme.bodyLarge),
              ],
            ),
          ),
          ListenableBuilder(
            listenable: controller,
            builder: (context, _) => _SystemStatusPill(
              status: controller.status,
              sensorFault: controller.data?.hasSensorFault ?? false,
            ),
          ),
          IconButton(
            onPressed: onRefresh,
            tooltip: '새로고침',
            iconSize: 28,
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
    );
  }
}

class _SystemStatusPill extends StatelessWidget {
  const _SystemStatusPill({required this.status, required this.sensorFault});

  final LoadStatus status;
  final bool sensorFault;

  @override
  Widget build(BuildContext context) {
    final (label, color, bg) = switch (status) {
      LoadStatus.loading => ('확인 중', HestiaColors.muted, const Color(0xFFF1EFED)),
      LoadStatus.offline => ('연결 끊김', HestiaColors.warning, HestiaColors.warningContainer),
      LoadStatus.error => ('오류', HestiaColors.safety, HestiaColors.safetyContainer),
      _ when sensorFault => ('센서 확인', HestiaColors.warning, HestiaColors.warningContainer),
      _ => ('정상', HestiaColors.ok, HestiaColors.okContainer),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(24),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 12, color: color),
          const SizedBox(width: 8),
          Text(
            label,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

/// 현재 상태. 값은 Context Engine이 준 그대로 보여준다.
class _ContextSection extends StatelessWidget {
  const _ContextSection({required this.hestiaContext});

  final HestiaContext hestiaContext;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ordered = hestiaContext.ordered;
    const primaryNames = {ContextName.away, ContextName.activity};
    final primary = ordered.where((s) => primaryNames.contains(s.name));
    final secondary = ordered.where((s) => !primaryNames.contains(s.name));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionHeader('현재 상태'),
        HestiaCard(
          onTap: () =>
              Navigator.of(context).pushNamed(AppRoutes.explanation),
          child: hestiaContext.isEmpty
              ? Text(
                  ContextLabels.state('UNKNOWN'),
                  style: theme.textTheme.titleLarge,
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final s in primary) _PrimaryState(state: s),
                    if (secondary.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          for (final s in secondary) _ContextChip(state: s),
                        ],
                      ),
                    ],
                    const SizedBox(height: 12),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        Text(
                          'HESTIA 판단 과정 보기',
                          style: theme.textTheme.titleMedium?.copyWith(
                            color: theme.colorScheme.primary,
                          ),
                        ),
                        Icon(
                          Icons.chevron_right_rounded,
                          color: theme.colorScheme.primary,
                        ),
                      ],
                    ),
                  ],
                ),
        ),
      ],
    );
  }
}

class _PrimaryState extends StatelessWidget {
  const _PrimaryState({required this.state});

  final ContextState state;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fault = ContextLabels.isFault(state.state);
    final color = fault ? HestiaColors.warning : theme.colorScheme.primary;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(ContextLabels.iconOf(state), size: 36, color: color),
          const SizedBox(width: 16),
          Expanded(
            child: Text(
              ContextLabels.describe(state),
              style: theme.textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          if (state.confidence != null)
            Text(
              '신뢰도 ${(state.confidence! * 100).round()}%',
              style: theme.textTheme.bodyMedium,
            ),
        ],
      ),
    );
  }
}

class _ContextChip extends StatelessWidget {
  const _ContextChip({required this.state});

  final ContextState state;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: scheme.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(ContextLabels.iconOf(state), size: 20),
          const SizedBox(width: 6),
          Text(
            '${ContextLabels.name(state.name)} · '
            '${ContextLabels.describe(state)}',
            style: const TextStyle(fontSize: 16),
          ),
        ],
      ),
    );
  }
}

/// 복약 카드. 등록 전에는 [추가하기]만, 등록 후에는 약 목록과 남은 기간.
class _MedicationSection extends StatelessWidget {
  const _MedicationSection({required this.medications, required this.onOpen});

  static const _maxRows = 3;

  final List<Medication> medications;
  final ValueChanged<String> onOpen;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (medications.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(top: 16),
        child: HestiaCard(
          onTap: () => onOpen(AppRoutes.medicationEdit),
          color: theme.colorScheme.surface,
          borderColor: theme.colorScheme.outlineVariant,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
          child: Row(
            children: [
              Icon(Icons.medication_rounded,
                  size: 32, color: theme.colorScheme.primary),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('복약 알림 추가하기',
                        style: theme.textTheme.titleMedium),
                    Text('드시는 약이 있으면 시간에 맞춰 알려드려요',
                        style: theme.textTheme.bodyMedium),
                  ],
                ),
              ),
              Icon(Icons.add_circle_outline_rounded,
                  size: 32, color: theme.colorScheme.primary),
            ],
          ),
        ),
      );
    }

    final now = DateTime.now();
    final shown = medications.take(_maxRows).toList();
    final hidden = medications.length - shown.length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionHeader(
          '복약',
          trailing: TextButton(
            onPressed: () => onOpen(AppRoutes.medications),
            child: const Text('관리'),
          ),
        ),
        HestiaCard(
          onTap: () => onOpen(AppRoutes.medications),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final (i, m) in shown.indexed) ...[
                if (i > 0) const Divider(height: 20),
                _MedicationLine(medication: m, now: now),
              ],
              if (hidden > 0)
                Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: Text('외 $hidden개', style: theme.textTheme.bodyMedium),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MedicationLine extends StatelessWidget {
  const _MedicationLine({required this.medication, required this.now});

  final Medication medication;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (period, warn) = MedicationLabels.period(medication, now);
    return Row(
      children: [
        Icon(Icons.medication_rounded, color: theme.colorScheme.primary),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(medication.name, style: theme.textTheme.titleMedium),
              Text(medication.scheduleLabel, style: theme.textTheme.bodyMedium),
            ],
          ),
        ),
        if (warn) ...[
          const Icon(Icons.warning_amber_rounded,
              size: 20, color: HestiaColors.warning),
          const SizedBox(width: 4),
        ],
        Text(
          period,
          style: theme.textTheme.titleSmall
              ?.copyWith(color: warn ? HestiaColors.warning : null),
        ),
      ],
    );
  }
}

class _RoomSection extends StatelessWidget {
  const _RoomSection({required this.data, required this.controller});

  final HomeData data;
  final HomeController controller;

  @override
  Widget build(BuildContext context) {
    if (data.rooms.isEmpty) return const SizedBox.shrink();
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionHeader('공간'),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              for (final room in data.rooms)
                FilterChip(
                  avatar: Icon(RoomLabels.icon(room.id), size: 22),
                  label: Text(
                    '${room.name}  '
                    '${data.devices.where((d) => d.roomId == room.id).length}',
                  ),
                  labelStyle: const TextStyle(fontSize: 18),
                  labelPadding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 6,
                  ),
                  showCheckmark: false,
                  selected: controller.roomFilter == room.id,
                  onSelected: (_) => controller.selectRoom(room.id),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _DeviceSection extends StatelessWidget {
  const _DeviceSection({required this.data, required this.controller});

  final HomeData data;
  final HomeController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final devices = controller.visibleDevices(data);
        final filter = controller.roomFilter;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SectionHeader(
              filter == null ? '가전' : '가전 · ${data.roomName(filter)}',
              trailing: filter == null
                  ? null
                  : TextButton(
                      onPressed: () => controller.selectRoom(null),
                      child: const Text('전체 보기'),
                    ),
            ),
            if (devices.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('등록된 가전이 없습니다.', style: TextStyle(fontSize: 18)),
              )
            else
              Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  for (final d in devices)
                    _DeviceTile(device: d, roomName: data.roomName(d.roomId)),
                ],
              ),
          ],
        );
      },
    );
  }
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile({required this.device, required this.roomName});

  final Device device;
  final String roomName;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final active = DeviceLabels.isActive(device);
    final offline = !device.online;
    final accent = offline
        ? HestiaColors.warning
        : (active ? scheme.primary : HestiaColors.muted);

    return SizedBox(
      width: 172,
      child: HestiaCard(
        padding: const EdgeInsets.all(16),
        color: active ? scheme.primaryContainer : null,
        borderColor: offline ? HestiaColors.warning : null,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(DeviceLabels.icon(device.type), size: 32, color: accent),
            const SizedBox(height: 10),
            Text(
              device.type.label,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            Text(
              roomName,
              style: TextStyle(fontSize: 14, color: scheme.onSurfaceVariant),
            ),
            const SizedBox(height: 6),
            Text(
              DeviceLabels.status(device),
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.w800,
                color: accent,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _RecentNotifications extends StatelessWidget {
  const _RecentNotifications({required this.onSeeAll});

  final VoidCallback onSeeAll;

  @override
  Widget build(BuildContext context) {
    final center = HestiaScope.of(context).notifications;
    return ListenableBuilder(
      listenable: center,
      builder: (context, _) {
        final recent = center.items.take(2).toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SectionHeader(
              '최근 알림',
              trailing: TextButton(
                onPressed: onSeeAll,
                child: const Text('전체 보기'),
              ),
            ),
            if (recent.isEmpty)
              const Text('새 알림이 없습니다.', style: TextStyle(fontSize: 18))
            else
              for (final n in recent)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: _RecentNotificationTile(notification: n),
                ),
          ],
        );
      },
    );
  }
}

class _RecentNotificationTile extends StatelessWidget {
  const _RecentNotificationTile({required this.notification});

  final HestiaNotification notification;

  @override
  Widget build(BuildContext context) {
    final style =
        NotificationStyle.of(notification, Theme.of(context).colorScheme);
    return HestiaCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      color: notification.isSafety ? style.container : null,
      borderColor: notification.isSafety ? style.color : null,
      onTap: () => Navigator.of(context).pushNamed(
        AppRoutes.notificationDetail,
        arguments: notification.id,
      ),
      child: Row(
        children: [
          Icon(style.icon, color: style.color, size: 30),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  notification.title,
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight:
                        notification.seen ? FontWeight.w500 : FontWeight.w800,
                  ),
                ),
                Text(
                  TimeFormat.relative(notification.createdAt, DateTime.now()),
                  style: const TextStyle(fontSize: 15),
                ),
              ],
            ),
          ),
          if (!notification.seen)
            Icon(Icons.circle, size: 12, color: style.color),
        ],
      ),
    );
  }
}
