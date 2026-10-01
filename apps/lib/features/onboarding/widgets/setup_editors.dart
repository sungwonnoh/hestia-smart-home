import 'package:flutter/material.dart';

import '../../../app/theme.dart';
import '../../../core/utils/display_labels.dart';
import '../../../models/device.dart';
import '../../../models/room.dart';
import '../../../models/user_preferences.dart';
import 'selection_tile.dart';

// 최초 설정과 설정 화면이 함께 쓰는 입력 위젯.

class RoomSelector extends StatelessWidget {
  const RoomSelector({
    super.key,
    required this.selectedIds,
    required this.onToggle,
  });

  final Set<String> selectedIds;
  final ValueChanged<String> onToggle;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 12,
      runSpacing: 12,
      children: [
        for (final t in RoomTemplate.catalog)
          SelectionTile(
            icon: RoomLabels.icon(t.id),
            label: t.name,
            caption: t.description,
            selected: selectedIds.contains(t.id),
            onTap: () => onToggle(t.id),
          ),
      ],
    );
  }
}

class DeviceSelector extends StatelessWidget {
  const DeviceSelector({
    super.key,
    required this.selected,
    required this.onToggle,
  });

  final Set<DeviceType> selected;
  final ValueChanged<DeviceType> onToggle;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 12,
      runSpacing: 12,
      children: [
        for (final type in DeviceType.selectable)
          SelectionTile(
            icon: DeviceLabels.icon(type),
            label: type.label,
            selected: selected.contains(type),
            onTap: () => onToggle(type),
          ),
      ],
    );
  }
}

/// 가전 하나의 위치를 고르는 행.
class DevicePlacementRow extends StatelessWidget {
  const DevicePlacementRow({
    super.key,
    required this.icon,
    required this.title,
    required this.rooms,
    required this.selectedRoomId,
    required this.onSelected,
  });

  final IconData icon;
  final String title;
  final List<Room> rooms;
  final String? selectedRoomId;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(HestiaSizes.radius),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 28, color: scheme.primary),
              const SizedBox(width: 12),
              Text(title, style: Theme.of(context).textTheme.titleMedium),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              for (final room in rooms)
                ChoiceChip(
                  label: Text(room.name),
                  selected: room.id == selectedRoomId,
                  onSelected: (_) => onSelected(room.id),
                  labelStyle: const TextStyle(fontSize: 18),
                  labelPadding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 6,
                  ),
                  materialTapTargetSize: MaterialTapTargetSize.padded,
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 알림/개인화 설정 폼.
class PreferencesForm extends StatefulWidget {
  const PreferencesForm({
    super.key,
    required this.value,
    required this.onChanged,
  });

  final UserPreferences value;
  final ValueChanged<UserPreferences> onChanged;

  @override
  State<PreferencesForm> createState() => _PreferencesFormState();
}

class _PreferencesFormState extends State<PreferencesForm> {
  late final TextEditingController _contact = TextEditingController(
    text: widget.value.safety.emergencyContact ?? '',
  );

  @override
  void dispose() {
    _contact.dispose();
    super.dispose();
  }

  Future<void> _pickTime({required bool start}) async {
    final quiet = widget.value.quietHours;
    final initial = start ? quiet.start : quiet.end;
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: initial.hour, minute: initial.minute),
      helpText: start ? '방해 금지 시작' : '방해 금지 종료',
    );
    if (picked == null) return;
    final time = DayTime(picked.hour, picked.minute);
    widget.onChanged(widget.value.copyWith(
      quietHours: start ? quiet.copyWith(start: time) : quiet.copyWith(end: time),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final prefs = widget.value;
    final quiet = prefs.quietHours;
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _Group(
          children: [
            SwitchListTile(
              title: const Text('일반 알림'),
              subtitle: const Text('생활 리마인더, 가전 상태 알림'),
              value: prefs.notificationsEnabled,
              onChanged: (v) =>
                  widget.onChanged(prefs.copyWith(notificationsEnabled: v)),
            ),
            const Divider(height: 1),
            SwitchListTile(
              title: const Text('방해 금지 시간'),
              subtitle: Text(
                quiet.enabled
                    ? '${quiet.start.format()} ~ ${quiet.end.format()}'
                    : '사용 안 함',
              ),
              value: quiet.enabled,
              onChanged: (v) => widget.onChanged(
                prefs.copyWith(quietHours: quiet.copyWith(enabled: v)),
              ),
            ),
            if (quiet.enabled)
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _pickTime(start: true),
                        child: Text('시작 ${quiet.start.format()}'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _pickTime(start: false),
                        child: Text('종료 ${quiet.end.format()}'),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ),
        const SizedBox(height: 16),
        _Group(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Text('알림 민감도', style: theme.textTheme.titleMedium),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: SegmentedButton<NotificationSensitivity>(
                segments: [
                  for (final s in NotificationSensitivity.values)
                    ButtonSegment(value: s, label: Text(s.label)),
                ],
                selected: {prefs.sensitivity},
                showSelectedIcon: false,
                style: SegmentedButton.styleFrom(
                  minimumSize: const Size(0, HestiaSizes.minTouch),
                  textStyle: const TextStyle(fontSize: 18),
                ),
                onSelectionChanged: (s) =>
                    widget.onChanged(prefs.copyWith(sensitivity: s.first)),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              child: Text(
                prefs.sensitivity.description,
                style: theme.textTheme.bodyMedium,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        _Group(
          color: HestiaColors.safetyContainer,
          children: [
            const ListTile(
              leading: Icon(
                Icons.health_and_safety_rounded,
                color: HestiaColors.safety,
                size: 32,
              ),
              title: Text('안전 알림 · 항상 켜짐'),
              subtitle: Text('위험 상황 알림은 방해 금지 시간과 관계없이 전달됩니다.'),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
              child: TextField(
                controller: _contact,
                keyboardType: TextInputType.phone,
                style: const TextStyle(fontSize: 18),
                decoration: const InputDecoration(
                  labelText: '비상 연락처 (선택)',
                  hintText: '010-0000-0000',
                  filled: true,
                  border: OutlineInputBorder(),
                ),
                onChanged: (v) => widget.onChanged(prefs.copyWith(
                  safety: SafetyPreferences(
                    emergencyContact: v.trim().isEmpty ? null : v.trim(),
                  ),
                )),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _Group extends StatelessWidget {
  const _Group({required this.children, this.color});

  final List<Widget> children;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: color ?? Theme.of(context).colorScheme.surfaceContainerLow,
      borderRadius: BorderRadius.circular(HestiaSizes.radius),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: children,
      ),
    );
  }
}
