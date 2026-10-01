import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../core/network/hestia_exception.dart';
import '../../core/utils/display_labels.dart';
import '../../core/widgets/state_views.dart';
import '../../models/device.dart';
import '../../models/home_setup.dart';
import '../../models/user_preferences.dart';
import '../onboarding/widgets/setup_editors.dart';
import 'setup_controller.dart';

/// 설정 편집 화면 공통 뼈대: 현재 설정 로드 → 초안 편집 → [저장].
class _SetupEditScaffold extends StatefulWidget {
  const _SetupEditScaffold({
    required this.title,
    required this.onLoaded,
    required this.bodyBuilder,
    required this.onSave,
    this.canSave,
  });

  final String title;
  final void Function(HomeSetup setup) onLoaded;
  final WidgetBuilder bodyBuilder;
  final Future<void> Function(SetupController controller) onSave;
  final bool Function()? canSave;

  @override
  State<_SetupEditScaffold> createState() => _SetupEditScaffoldState();
}

class _SetupEditScaffoldState extends State<_SetupEditScaffold> {
  late final SetupController _controller;
  bool _loaded = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _controller = SetupController(HestiaScope.of(context).repository)
      ..addListener(_onControllerChanged)
      ..load();
  }

  void _onControllerChanged() {
    final data = _controller.data;
    if (!_loaded && data != null) {
      _loaded = true;
      widget.onLoaded(data);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      await widget.onSave(_controller);
      messenger.showSnackBar(const SnackBar(content: Text('저장했습니다.')));
      navigator.pop();
    } on HestiaException catch (e) {
      messenger.showSnackBar(
        SnackBar(content: Text('저장하지 못했습니다. ${e.message}')),
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canSave = widget.canSave?.call() ?? true;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: AsyncStateView<HomeSetup>(
        controller: _controller,
        builder: (context, _) => Column(
          children: [
            Expanded(
              child: Scrollbar(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
                  child: widget.bodyBuilder(context),
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 20),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _saving || !canSave ? null : _save,
                  child: _saving
                      ? const SizedBox.square(
                          dimension: 28,
                          child: CircularProgressIndicator(strokeWidth: 3),
                        )
                      : const Text('저장'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class RoomsSettingsPage extends StatefulWidget {
  const RoomsSettingsPage({super.key});

  @override
  State<RoomsSettingsPage> createState() => _RoomsSettingsPageState();
}

class _RoomsSettingsPageState extends State<RoomsSettingsPage> {
  final Set<String> _roomIds = {};

  @override
  Widget build(BuildContext context) {
    return _SetupEditScaffold(
      title: '공간 관리',
      onLoaded: (setup) => setState(() {
        _roomIds
          ..clear()
          ..addAll(setup.rooms.map((r) => r.id));
      }),
      canSave: () => _roomIds.isNotEmpty,
      onSave: (c) => c.saveRooms(_roomIds),
      bodyBuilder: (context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '공간을 빼면 그곳에 있던 가전은 위치 미지정이 됩니다.',
            style: Theme.of(context).textTheme.bodyLarge,
          ),
          const SizedBox(height: 16),
          RoomSelector(
            selectedIds: _roomIds,
            onToggle: (id) => setState(() {
              if (!_roomIds.remove(id)) _roomIds.add(id);
            }),
          ),
        ],
      ),
    );
  }
}

class DevicesSettingsPage extends StatefulWidget {
  const DevicesSettingsPage({super.key});

  @override
  State<DevicesSettingsPage> createState() => _DevicesSettingsPageState();
}

class _DevicesSettingsPageState extends State<DevicesSettingsPage> {
  HomeSetup? _setup;
  final Map<DeviceType, String?> _placements = {};

  void _toggle(DeviceType type) => setState(() {
        if (_placements.containsKey(type)) {
          _placements.remove(type);
        } else {
          final rooms = _setup?.rooms ?? const [];
          _placements[type] = type.preferredRoomIds.firstWhere(
            (id) => rooms.any((r) => r.id == id),
            orElse: () => rooms.isEmpty ? '' : rooms.first.id,
          );
        }
      });

  @override
  Widget build(BuildContext context) {
    final rooms = _setup?.rooms ?? const [];
    final theme = Theme.of(context);
    return _SetupEditScaffold(
      title: '가전 관리',
      onLoaded: (setup) => setState(() {
        _setup = setup;
        _placements
          ..clear()
          ..addAll({
            for (final d in setup.devices)
              d.type: d.roomId.isEmpty ? null : d.roomId,
          });
      }),
      canSave: () => _placements.values.every((v) => v != null && v.isNotEmpty),
      onSave: (c) => c.saveDevices(
        {for (final e in _placements.entries) e.key: e.value!},
      ),
      bodyBuilder: (context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('사용하는 가전', style: theme.textTheme.titleLarge),
          const SizedBox(height: 12),
          DeviceSelector(
            selected: _placements.keys.toSet(),
            onToggle: _toggle,
          ),
          const SizedBox(height: 24),
          Text('가전 위치', style: theme.textTheme.titleLarge),
          const SizedBox(height: 12),
          if (_placements.isEmpty)
            Text('선택한 가전이 없습니다.', style: theme.textTheme.bodyLarge)
          else
            for (final type in DeviceType.selectable)
              if (_placements.containsKey(type))
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: DevicePlacementRow(
                    icon: DeviceLabels.icon(type),
                    title: _placements[type] == null
                        ? '${type.label} · 위치를 선택하세요'
                        : type.label,
                    rooms: rooms,
                    selectedRoomId: _placements[type],
                    onSelected: (roomId) =>
                        setState(() => _placements[type] = roomId),
                  ),
                ),
        ],
      ),
    );
  }
}

class NotificationSettingsPage extends StatefulWidget {
  const NotificationSettingsPage({super.key});

  @override
  State<NotificationSettingsPage> createState() =>
      _NotificationSettingsPageState();
}

class _NotificationSettingsPageState extends State<NotificationSettingsPage> {
  UserPreferences? _draft;

  @override
  Widget build(BuildContext context) {
    return _SetupEditScaffold(
      title: '알림 설정',
      onLoaded: (setup) => setState(() => _draft = setup.preferences),
      onSave: (c) => c.savePreferences(_draft ?? const UserPreferences()),
      bodyBuilder: (context) => _draft == null
          ? const LoadingView()
          : PreferencesForm(
              value: _draft!,
              onChanged: (v) => setState(() => _draft = v),
            ),
    );
  }
}
